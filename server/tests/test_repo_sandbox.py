"""Integration tests for the Monty sandbox wrapper.

These run a REAL Monty interpreter (no network, microsecond startup). They
exist because the two behaviors that would silently break this feature in
production — per-feed mounts and cumulative-budget session poisoning — are
runtime properties of Monty, not of our code.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.llm.repo import sandbox as sandbox_module
from app.llm.repo.sandbox import RepoSandbox, RepoSnapshot

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SOURCE = "MARKER = 'hello-from-repo'\n\n\ndef add(a, b):\n    return a + b\n"


@pytest.fixture()
def snapshot(tmp_path: Path) -> RepoSnapshot:
    root = tmp_path / "snap"
    root.mkdir()
    (root / "main.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "outside.txt").write_text("host secret\n", encoding="utf-8")
    return RepoSnapshot(
        paper_id="66666666-6666-6666-6666-666666666666",
        owner="o",
        repo="r",
        ref="main",
        commit_sha="a" * 40,
        root=root,
        files=[{"path": "main.py", "size": len(SOURCE)}],
    )


def _run(sandbox: RepoSandbox, code: str):
    return asyncio.run(sandbox.run(code))


@pytest.fixture()
def live_sandbox(snapshot):
    sandbox = RepoSandbox(snapshot)
    asyncio.run(sandbox.open())
    if sandbox._open_error:
        sandbox.close()
        pytest.skip(f"Monty unavailable: {sandbox._open_error}")
    yield sandbox
    sandbox.close()


def test_mount_is_readable_and_state_persists(live_sandbox):
    first = _run(live_sandbox, "x = open('/repo/main.py').read()\nprint(len(x))")
    assert first["output"].strip() == str(len(SOURCE))
    assert first["files"] == []  # raw open() isn't a prelude helper
    # The mount must be passed on EVERY feed — a later read proves it is.
    second = _run(
        live_sandbox, "print('MARKER' in x)\nprint(open('/repo/main.py').read()[:5])"
    )
    assert "True" in second["output"]


def test_prelude_helpers_are_callable_and_record_touched_files(live_sandbox):
    result = _run(live_sandbox, "print(read('/repo/main.py'))")
    assert "MARKER = 'hello-from-repo'" in result["output"]
    assert result["files"] == ["main.py"]

    grep_result = _run(live_sandbox, "print(grep('def add'))")
    assert "main.py" in grep_result["output"]
    assert grep_result["files"] == ["main.py"]

    tree_result = _run(live_sandbox, "print(tree('/repo'))")
    assert "main.py" in tree_result["output"]


def test_writes_and_escapes_are_denied(live_sandbox):
    write = _run(live_sandbox, "open('/repo/x.txt','w').write('a')")
    assert "PermissionError" in write["output"]
    escape = _run(live_sandbox, "print(open('/etc/passwd').read())")
    assert "PermissionError" in escape["output"]
    outside = _run(live_sandbox, "print(open('/repo/../outside.txt').read())")
    assert "host secret" not in outside["output"]


def test_errors_come_back_as_tracebacks(live_sandbox):
    result = _run(live_sandbox, "import os\nprint(os.walk('/repo'))")
    assert "AttributeError" in result["output"]
    assert "Traceback" in result["output"]
    # ...and the session survives an error.
    assert "2" in _run(live_sandbox, "print(1 + 1)")["output"]


def test_empty_and_oversized_code_are_rejected_without_a_feed(live_sandbox):
    assert "empty code" in _run(live_sandbox, "   ")["output"]
    assert "too long" in _run(live_sandbox, "x" * 20_001)["output"]


def test_poisoned_session_is_detected_and_rebuilt(snapshot, monkeypatch):
    """Exhausting the cumulative budget poisons the session permanently —
    every later feed fails instantly. The wrapper must notice and rebuild."""
    monkeypatch.setattr(sandbox_module, "SESSION_DURATION_BUDGET", 2.0)
    sandbox = RepoSandbox(snapshot)
    asyncio.run(sandbox.open())
    if sandbox._open_error:
        sandbox.close()
        pytest.skip(f"Monty unavailable: {sandbox._open_error}")
    try:
        assert "7" in _run(sandbox, "marker = 7\nprint(marker)")["output"]

        burned = _run(sandbox, "i = 0\nwhile True:\n    i += 1\n")
        assert "RESET" in burned["output"]
        assert sandbox.resets == 1

        # The rebuilt session works...
        assert "2" in _run(sandbox, "print(1 + 1)")["output"]
        # ...and is genuinely fresh: the old variable is gone.
        assert "NameError" in _run(sandbox, "print(marker)")["output"]
        # The mount survives the rebuild (it is re-passed per feed).
        assert (
            "MARKER" in _run(sandbox, "print(open('/repo/main.py').read())")["output"]
        )
    finally:
        sandbox.close()


def test_output_is_capped_for_the_model(live_sandbox):
    cap = sandbox_module.MAX_TOOL_OUTPUT
    result = _run(live_sandbox, "print('z' * 50000)")
    assert result["output"].startswith("z" * cap)
    assert len(result["output"]) <= cap + 300
    # The notice says how much was dropped and how to narrow the call.
    assert "[output truncated:" in result["output"]
    assert f"{50000 - cap:,} more chars" in result["output"]
    assert result["files"] == []


def test_full_read_window_fits_in_one_call(tmp_path: Path):
    """A 400-line read() of ordinary source must not be cut by the model cap —
    that was the whole point of decoupling it from the UI wire cap."""
    from app.llm.repo.prelude import MAX_READ_LINES

    root = tmp_path / "snap2"
    root.mkdir()
    body = "\n".join(
        f"result_{i} = transform(inputs[{i}], alpha=0.5)  # step"
        for i in range(MAX_READ_LINES)
    )
    (root / "big.py").write_text(body, encoding="utf-8")
    snapshot = RepoSnapshot(
        paper_id="66666666-6666-6666-6666-666666666666",
        owner="o",
        repo="r",
        ref="main",
        commit_sha="a" * 40,
        root=root,
        files=[{"path": "big.py", "size": len(body)}],
    )
    sandbox = RepoSandbox(snapshot)
    asyncio.run(sandbox.open())
    if sandbox._open_error:
        sandbox.close()
        pytest.skip(f"Monty unavailable: {sandbox._open_error}")
    try:
        result = _run(sandbox, "print(read('/repo/big.py'))")
    finally:
        sandbox.close()
    assert "[output truncated" not in result["output"]
    assert f"{MAX_READ_LINES}| " in result["output"]
    assert result["files"] == ["big.py"]


def test_cumulative_output_budget_reduces_later_caps(live_sandbox):
    first = _run(live_sandbox, "print('a' * 100)")
    assert live_sandbox.output_chars == len(first["output"])
    live_sandbox.output_chars = sandbox_module.RUN_OUTPUT_BUDGET
    result = _run(live_sandbox, "print('z' * 10000)")
    small_cap = sandbox_module.MAX_TOOL_OUTPUT_AFTER_BUDGET
    assert result["output"].startswith("z" * small_cap)
    assert len(result["output"]) <= small_cap + 300
    assert "[output truncated:" in result["output"]


def test_tool_payload_keeps_files_through_the_wire_truncator(live_sandbox):
    from app.llm.chat.stream import truncate_tool_output

    result = _run(live_sandbox, "print(read('/repo/main.py'))\nprint('z' * 8000)")
    wired = truncate_tool_output(result)
    assert wired["truncated"] is True
    # The chips survive, and the preview is the output text, not JSON.
    assert wired["files"] == ["main.py"]
    assert wired["preview"].startswith("/repo/main.py (lines 1-")


def test_closed_sandbox_returns_a_message_not_an_exception(snapshot):
    sandbox = RepoSandbox(snapshot)
    asyncio.run(sandbox.open())
    sandbox.close()
    result = _run(sandbox, "print(1)")
    assert result["files"] == []
    assert "sandbox" in result["output"].lower()


def test_missing_sandbox_degrades_in_the_tool_dispatcher():
    from types import SimpleNamespace

    from app.llm.chat.paper import _run_repo_tool

    deps = SimpleNamespace(repo_sandbox=None)
    result = asyncio.run(_run_repo_tool(deps, "print(1)"))
    assert result["files"] == []
    assert "No code repository" in result["output"]


def test_open_finishing_after_close_releases_its_resources(snapshot, monkeypatch):
    """A cancelled turn runs close() while the checkout is still in flight on
    the executor thread; the late session and mount must not leak."""
    released = {"session": False, "mount": False}

    class _Mount:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def close(self):
            released["mount"] = True

    class _Session:
        def __exit__(self, *args):
            released["session"] = True

    monkeypatch.setattr(sandbox_module, "MountDir", _Mount)
    monkeypatch.setattr(sandbox_module, "_get_pool", lambda: object())
    monkeypatch.setattr(sandbox_module, "_checkout", lambda pool: _Session())

    sandbox = RepoSandbox(snapshot)
    sandbox.close()  # the turn was torn down first...
    sandbox._open_sync()  # ...then the executor thread finished opening
    assert released == {"session": True, "mount": True}
    assert sandbox._session is None and sandbox._mount is None


def test_reset_after_close_does_not_reattach_a_session(snapshot, monkeypatch):
    exited = []

    class _Session:
        def __exit__(self, *args):
            exited.append(self)

    monkeypatch.setattr(sandbox_module, "_get_pool", lambda: object())
    monkeypatch.setattr(sandbox_module, "_checkout", lambda pool: _Session())
    sandbox = RepoSandbox(snapshot)
    sandbox.close()
    sandbox._reset_session()
    assert sandbox._session is None
    assert len(exited) == 1
