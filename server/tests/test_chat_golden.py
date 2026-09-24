"""Golden runs: what goes on the wire, into the DB and back to the model.

Each scenario drives a real entry point (`run_paper_chat`,
`run_quick_question`, `build_paper_agent`) against a scripted pydantic-ai
`FunctionModel` and compares everything observable with a recorded golden
file under `tests/fixtures/chat_golden/`:

- the encoded UI-message stream parts, in order;
- the rows the turn persisted (roles, content, references, bucket);
- the tool definitions the model was offered and the tool returns it got
  back (budget refusals included);
- the telemetry events.

The goldens were recorded on the pre-restructure runtime, so these tests pin
the Phase 5 chat-runtime split to "no observable change". Random ids and
timestamps are normalized (first-seen order), nothing else is.

Every I/O seam is patched by NAME in whichever `app.llm.chat` module holds
it (`_patch_everywhere`), so the tests don't care which module a helper
lives in. Regenerate with `UPDATE_CHAT_GOLDEN=1` only for an intended
behaviour change.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    FunctionModel,
)
from pydantic_ai.models.wrapper import WrapperModel

from app.llm.chat.paper import PaperAgentDeps, build_paper_agent
from app.llm.model_registry import LLMProvider, ModelSpec
from app.llm.repo import storage
from app.llm.retrying_model import RetryingModel

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "chat_golden"
UPDATE = bool(os.environ.get("UPDATE_CHAT_GOLDEN"))

PAPER_ID = "11111111-2222-3333-4444-555555555555"
CONVERSATION_ID = "66666666-7777-8888-9999-000000000000"
SHA = "d" * 40
SMALL_FILE = "\n".join(f"line {i}" for i in range(1, 21))

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?")
# Fixed ids the scripts use; kept verbatim so a moved id is visible.
_STABLE_IDS = {PAPER_ID, CONVERSATION_ID}


def _normalize(value: Any) -> Any:
    """Replace random uuids (first-seen order) and timestamps, recursively."""
    seen: Dict[str, str] = {}

    def fix(text: str) -> str:
        def sub_uuid(match: re.Match[str]) -> str:
            found = match.group(0)
            if found in _STABLE_IDS:
                return found
            if found not in seen:
                seen[found] = f"<uuid-{len(seen) + 1}>"
            return seen[found]

        return _TS_RE.sub("<ts>", _UUID_RE.sub(sub_uuid, text))

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {fix(str(k)): walk(v) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [walk(item) for item in node]
        if isinstance(node, str):
            return fix(node)
        if isinstance(node, uuid.UUID):
            return fix(str(node))
        return node

    return walk(value)


def _check_golden(name: str, observed: Dict[str, Any]) -> None:
    path = GOLDEN_DIR / f"{name}.json"
    normalized = json.loads(json.dumps(_normalize(observed), default=str))
    if UPDATE or not path.exists():
        if not UPDATE:
            pytest.fail(f"missing golden {path}; run with UPDATE_CHAT_GOLDEN=1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(normalized, indent=1, sort_keys=True) + "\n")
        return
    expected = json.loads(path.read_text())
    assert normalized == expected


def _patch_everywhere(monkeypatch, name: str, value: Any) -> None:
    """Patch `name` on every loaded `app.llm.chat` module that defines it."""
    hits = 0
    for module_name, module in list(sys.modules.items()):
        if module is None or not module_name.startswith("app.llm.chat"):
            continue
        if name in vars(module):
            monkeypatch.setattr(module, name, value)
            hits += 1
    assert hits, f"nothing named {name} in app.llm.chat"


def _parse_sse(encoded: List[str]) -> List[Any]:
    out: List[Any] = []
    for raw in encoded:
        assert raw.startswith("data: ") and raw.endswith("\n\n"), raw
        body = raw[len("data: ") : -2]
        out.append("[DONE]" if body == "[DONE]" else json.loads(body))
    return out


def _tool_defs(info: AgentInfo) -> List[Dict[str, Any]]:
    return [dataclasses.asdict(tool) for tool in info.function_tools]


# =====================================================================
# scripted models
# =====================================================================


def _stream_script(script: List[List[Any]], seen_tools: List[Any]) -> FunctionModel:
    """FunctionModel that streams `script[i]` for the i-th request."""
    state = {"index": 0}

    async def stream_fn(messages: List[ModelMessage], info: AgentInfo):
        if not seen_tools:
            seen_tools.append(_tool_defs(info))
        index = min(state["index"], len(script) - 1)
        state["index"] += 1
        for item in script[index]:
            if isinstance(item, Exception):
                raise item
            yield item

    return FunctionModel(stream_function=stream_fn)


class _FlakyEntry(WrapperModel):
    """Fails `failures` times while ENTERING the stream, then delegates."""

    def __init__(self, wrapped, *, failures: int) -> None:
        super().__init__(wrapped)
        self.remaining = failures

    @asynccontextmanager
    async def request_stream(
        self, messages, model_settings, model_request_parameters, run_context=None
    ):
        if self.remaining > 0:
            self.remaining -= 1
            raise ModelHTTPError(status_code=503, model_name="scripted", body=None)
        async with self.wrapped.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response:
            yield response


def _call(name: str, args: Dict[str, Any], call_id: str) -> DeltaToolCall:
    return DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=call_id)


# =====================================================================
# fakes shared by the scenarios
# =====================================================================


class _FakeMessageCrud:
    def __init__(self) -> None:
        self.rows: List[Any] = []
        self.log: List[Dict[str, Any]] = []

    def get_all_conversation_messages(self, db, *, conversation_id, current_user):
        return list(self.rows)

    def create(self, db, *, obj_in, user):
        row = SimpleNamespace(
            id=obj_in.id or uuid.uuid4(),
            role=obj_in.role,
            content=obj_in.content,
            references=obj_in.references,
            bucket=obj_in.bucket,
        )
        self.log.append(
            {
                "op": "create",
                "id": str(row.id),
                "role": row.role,
                "content": row.content,
                "references": row.references,
                "bucket": row.bucket,
            }
        )
        return row

    def update(self, db, *, db_obj, obj_in, user):
        self.log.append(
            {
                "op": "update",
                "id": str(db_obj.id),
                "fields": obj_in.model_dump(exclude_unset=True, mode="json"),
            }
        )
        return db_obj

    def remove(self, db, *, id, user=None):
        self.log.append({"op": "remove", "id": str(id)})


class _World:
    """All I/O for one scenario, wired to fakes."""

    def __init__(self, monkeypatch, model: Any, *, repo_snapshot: Any = None):
        self.model = model
        self.events: List[Dict[str, Any]] = []
        self.crud = _FakeMessageCrud()
        self.user = SimpleNamespace(id=uuid.UUID(int=7))
        self.spec = ModelSpec(
            id="scripted-model", provider=LLMProvider.OPENAI, display_name="Scripted"
        )
        world = self

        paper = SimpleNamespace(
            id=PAPER_ID, title="A paper", abstract="abs", page_count=3
        )
        self.context = SimpleNamespace(
            paper=paper,
            context_mode="adaptive",
            system_prompt="be helpful",
            supplementary_papers=[],
            allowed_paper_ids=[PAPER_ID],
            family_index={PAPER_ID: paper},
            repo_snapshot=repo_snapshot,
            preload="PRELOAD",
        )
        conversation = SimpleNamespace(
            id=CONVERSATION_ID,
            conversable_type="paper",
            conversable_id=PAPER_ID,
        )
        from app.database.models import ConversableType

        conversation.conversable_type = ConversableType.PAPER

        class FakeRegistry:
            def resolve(self, provider=None, model_id=None, role=None):
                return world.spec

            def build_model(self, spec):
                return world.model

            def build_settings(self, spec, reasoning_effort=None, **kwargs):
                return None

        def record(name: str, properties: Optional[Dict[str, Any]] = None, **kw):
            props = dict(properties or kw.get("properties") or {})
            for noisy in ("time_taken", "duration_ms"):
                props.pop(noisy, None)
            world.events.append({"name": name, "properties": props})

        async def fake_reconcile(citations, *args, **kwargs):
            return [dict(c, matched_via="golden") for c in citations]

        def instant_retrying(model, *, on_retry=None):
            async def sleep(delay: float) -> None:
                return None

            return RetryingModel(model, on_retry=on_retry, sleep=sleep)

        fake_session = SimpleNamespace(
            close=lambda: None, get=lambda *a, **k: None, rollback=lambda: None
        )

        mp = monkeypatch
        # Make sure every chat module is loaded before patching by name.
        import app.llm.chat.quick_question  # noqa: F401
        import app.llm.chat.runtime  # noqa: F401

        _patch_everywhere(mp, "get_registry", lambda: FakeRegistry())
        _patch_everywhere(mp, "build_paper_chat_context", lambda *a, **k: self.context)
        _patch_everywhere(mp, "message_crud", self.crud)
        _patch_everywhere(mp, "track_event", record)
        _patch_everywhere(mp, "RetryingModel", instant_retrying)
        _patch_everywhere(mp, "rename_conversation", lambda **kwargs: None)
        _patch_everywhere(mp, "reconcile_citations", fake_reconcile)
        _patch_everywhere(mp, "SessionLocal", lambda: fake_session)
        mp.setattr("app.database.database.SessionLocal", lambda: fake_session)
        from app.database.crud.conversation_crud import conversation_crud

        mp.setattr(conversation_crud, "get", lambda *a, **k: conversation)

        # Paper tools: deterministic, no DB.
        from app.llm.chat import paper as paper_module

        def fake_tool(tool: str):
            def run(**kwargs: Any) -> Dict[str, Any]:
                kwargs.pop("db", None)
                kwargs.pop("current_user", None)
                return {"tool": tool, "args": kwargs, "hits": [1, 2]}

            return run

        for tool in ("read_section", "read_pages", "search_paper", "list_docs"):
            mp.setattr(paper_module, tool, fake_tool(tool))

    def run_paper_chat(self, *, text: str, message_id: str = "client-1") -> List[Any]:
        from app.llm.chat.runtime import run_paper_chat
        from app.llm.chat.stream import OpenPaperAdapter

        payload = {
            "id": CONVERSATION_ID,
            "trigger": "submit-message",
            "messages": [
                {
                    "id": message_id,
                    "role": "user",
                    "parts": [{"type": "text", "text": text, "state": "done"}],
                }
            ],
        }
        run_input = OpenPaperAdapter.build_run_input(json.dumps(payload).encode())

        async def drive() -> List[str]:
            out: List[str] = []
            async for encoded in run_paper_chat(
                db=SimpleNamespace(rollback=lambda: None),
                current_user=self.user,
                run_input=run_input,
                accept=None,
                paper_id=PAPER_ID,
                conversation_id=CONVERSATION_ID,
                provider=None,
                model=None,
                reasoning_effort=None,
                context_mode=None,
                user_references=["a quoted passage"],
            ):
                out.append(encoded)
            return out

        return _parse_sse(asyncio.run(drive()))


# =====================================================================
# paper chat
# =====================================================================

EVIDENCE_ANSWER = [
    "Attention is ",
    "all you need.\n\n---EVI",
    'DENCE---\n@cite[1|page=1]\n"quoted"\n---END-',
    "EVIDENCE---",
]


def test_paper_chat_tool_turn(monkeypatch):
    tools: List[Any] = []
    model = _stream_script(
        [
            [
                "Let me look.",
                {0: _call("search_paper", {"query": "attention"}, "call-1")},
                {1: _call("read_pages", {"start": 1, "end": 2}, "call-2")},
            ],
            [{0: _call("list_docs", {}, "call-3")}],
            EVIDENCE_ANSWER,
        ],
        tools,
    )
    world = _World(monkeypatch, _FlakyEntry(model, failures=2))
    chunks = world.run_paper_chat(text="what is attention?")
    _check_golden(
        "paper_chat_tool_turn",
        {
            "chunks": chunks,
            "rows": world.crud.log,
            "events": world.events,
            "tools": tools,
        },
    )


def test_paper_chat_mid_stream_failure(monkeypatch):
    tools: List[Any] = []
    model = _stream_script(
        [
            [
                "Partial answer ",
                ModelHTTPError(status_code=400, model_name="scripted", body="bad"),
            ]
        ],
        tools,
    )
    world = _World(monkeypatch, model)
    chunks = world.run_paper_chat(text="this will fail")
    _check_golden(
        "paper_chat_mid_stream_failure",
        {"chunks": chunks, "rows": world.crud.log, "events": world.events},
    )


# =====================================================================
# quick question
# =====================================================================


@pytest.fixture()
def snapshot(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    tree = storage.snapshot_dir(PAPER_ID, SHA) / storage.TREE_SUBDIR
    (tree / "pkg").mkdir(parents=True)
    (tree / "pkg" / "mod.py").write_text(SMALL_FILE, encoding="utf-8")
    storage.write_manifest(
        storage.snapshot_dir(PAPER_ID, SHA),
        {
            "owner": "o",
            "repo": "r",
            "ref": "main",
            "commit_sha": SHA,
            "files": [{"path": "pkg/mod.py", "size": len(SMALL_FILE)}],
        },
    )
    (storage.snapshot_dir(PAPER_ID, SHA) / storage.DONE_MARKER).write_text("ok")

    class _Crud:
        @staticmethod
        def get_ready_for_paper(session, *, paper_id):
            return SimpleNamespace(commit_sha=SHA)

    monkeypatch.setattr("app.database.crud.paper_repo_crud.paper_repo_crud", _Crud)


def test_quick_question_lookups_and_budget(monkeypatch, snapshot):
    tools: List[Any] = []
    read = {"path": "/repo/pkg/mod.py", "start": 1, "end": 3}
    script = [
        [{0: _call("tree", {"path": "/repo"}, "q-1")}],
        # A parallel burst past the four-lookup budget, plus two refusals
        # that must not be charged.
        [
            {
                0: _call("read_file", read, "q-2"),
                1: _call("read_file", {"path": "  "}, "q-3"),
                2: _call("grep_repo", {"pattern": "line", "glob": "*.py"}, "q-4"),
                3: _call("read_file", read, "q-5"),
                4: _call("grep_repo", {"pattern": ""}, "q-6"),
                5: _call("read_file", read, "q-7"),
                6: _call("tree", {}, "q-8"),
            }
        ],
        ["It prints ", "the first lines."],
    ]
    model = _stream_script(script, tools)
    world = _World(monkeypatch, _FlakyEntry(model, failures=1))

    from app.llm.chat.quick_question import run_quick_question

    async def drive() -> List[str]:
        return [
            encoded
            async for encoded in run_quick_question(
                db=SimpleNamespace(),
                current_user=world.user,
                accept=None,
                paper_id=PAPER_ID,
                question="What does this do?",
                file_path="pkg/mod.py",
                start_line=1,
                end_line=3,
                provider=None,
                model=None,
                reasoning_effort=None,
            )
        ]

    chunks = _parse_sse(asyncio.run(drive()))
    _check_golden(
        "quick_question_lookups_and_budget",
        {"chunks": chunks, "events": world.events, "tools": tools},
    )


# =====================================================================
# paper-agent tool budgets (agent level: every tool return)
# =====================================================================


class _FakeSandbox:
    def __init__(self) -> None:
        self.runs = 0

    async def run(self, code: str) -> Dict[str, Any]:
        self.runs += 1
        await asyncio.sleep(0)
        return {"files": ["/repo/a.py"], "output": f"ran {code}"}


def _run_budget_script(monkeypatch, batch: List[tuple]) -> Dict[str, Any]:
    world = _World(monkeypatch, None)
    tools: List[Any] = []

    def respond(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not tools:
            tools.append(_tool_defs(info))
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(name, args, tool_call_id=f"b-{index}")
                    for index, (name, args) in enumerate(batch)
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    paper = world.context.paper
    agent = build_paper_agent(
        model=FunctionModel(respond),
        spec=world.spec,
        system_prompt="system",
        paper=paper,
        context_mode="adaptive",
        repo_snapshot=SimpleNamespace(owner="o", repo="r"),
    )
    sandbox = _FakeSandbox()
    deps = PaperAgentDeps(
        paper_id=PAPER_ID,
        paper=paper,
        current_user=world.user,
        db=None,
        context_mode="adaptive",
        allowed_paper_ids=[PAPER_ID],
        repo_sandbox=sandbox,
    )
    result = agent.run_sync("go", deps=deps)
    returns = [
        [part.tool_call_id, part.tool_name, part.content]
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    ]
    return {
        "returns": sorted(returns, key=lambda r: int(r[0].split("-")[1])),
        "sandbox_runs": sandbox.runs,
        "events": sorted(
            (e for e in world.events if e["name"] == "paper_repo_run_python"),
            key=lambda e: json.dumps(e, sort_keys=True),
        ),
        "tools": tools,
    }


def test_shared_budget_refuses_past_sixty_calls(monkeypatch):
    batch = [("search_paper", {"query": f"q{i}"}) for i in range(60)]
    batch += [
        ("run_python", {"code": "print(1)"}),
        ("read_pages", {"start": 1, "end": 1}),
    ]
    _check_golden("budget_shared", _run_budget_script(monkeypatch, batch))


def test_repo_sub_budget_refuses_past_fifty_runs(monkeypatch):
    batch = [("run_python", {"code": f"print({i})"}) for i in range(52)]
    batch += [("list_docs", {})]
    _check_golden("budget_repo", _run_budget_script(monkeypatch, batch))
