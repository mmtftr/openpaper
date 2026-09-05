"""The `run_python` tool: a persistent Monty session with the repo mounted read-only.

Phase-0 findings this encodes:
  * `max_duration_secs` is a CUMULATIVE per-session execution budget. Once
    exhausted the session is permanently poisoned -- every later feed raises
    TimeoutError instantly (in ~0ms). Same for `max_memory`. So we detect
    poisoning and transparently rebuild the session, telling the model its
    variables are gone.
  * A mount is per-feed: it must be passed to EVERY feed_run call.
  * `external_lookup` is likewise per-feed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_monty import (
    CollectStreams,
    Monty,
    MontyCrashedError,
    MontyError,
    MontyRuntimeError,
    MontySyntaxError,
    MontyTypingError,
    MountDir,
)

from helpers import RepoHelpers

MAX_TOOL_OUTPUT = 6000  # mirrors prod's tool-output cap

# Two different guards, and they behave differently (see probe3_limits.py):
#   * PER_CALL_TIMEOUT is the pool's parent-side `request_timeout`. It is
#     genuinely per-call: a snippet exceeding it kills the worker and raises
#     MontyCrashedError(timed_out=True). Session state is lost but we rebuild.
#   * SESSION_DURATION_BUDGET is Monty's own `max_duration_secs`, which is a
#     CUMULATIVE execution budget for the whole session. Exhausting it poisons
#     the session permanently, so it works as a total-conversation cap only.
PER_CALL_TIMEOUT = 25.0
SESSION_DURATION_BUDGET = 240.0
SESSION_MEMORY_BUDGET = 512 * 1024 * 1024


@dataclass
class CallRecord:
    n: int
    code: str
    output: str
    ok: bool
    error_class: str | None
    elapsed: float
    truncated: bool
    session_reset: bool = False


@dataclass
class SandboxStats:
    calls: list[CallRecord] = field(default_factory=list)

    @property
    def n_calls(self) -> int:
        return len(self.calls)

    @property
    def n_errors(self) -> int:
        return sum(1 for c in self.calls if not c.ok)

    @property
    def n_resets(self) -> int:
        return sum(1 for c in self.calls if c.session_reset)

    @property
    def sandbox_seconds(self) -> float:
        return round(sum(c.elapsed for c in self.calls), 2)

    def summary(self) -> dict:
        return {
            "n_calls": self.n_calls,
            "n_errors": self.n_errors,
            "n_resets": self.n_resets,
            "sandbox_seconds": self.sandbox_seconds,
            "error_classes": [c.error_class for c in self.calls if not c.ok],
        }


class RepoSandbox:
    """Owns the Monty pool, the mount, and one long-lived REPL session."""

    def __init__(self, repo_root: Path, *, prelude: bool, virtual_root: str = "/repo"):
        self.repo_root = Path(repo_root)
        self.virtual_root = virtual_root
        self.prelude = prelude
        self.helpers = RepoHelpers(self.repo_root, virtual_root)
        self.stats = SandboxStats()
        self._pool: Monty | None = None
        self._mount: MountDir | None = None
        self._session = None
        self._poisoned = False

    # ---- lifecycle --------------------------------------------------------
    def __enter__(self) -> "RepoSandbox":
        self._pool = Monty(request_timeout=PER_CALL_TIMEOUT)
        self._pool.__enter__()
        self._mount = MountDir(
            host_path=self.repo_root,
            virtual_path=self.virtual_root,
            mode="read-only",
            # Default is 100 MB, and it covers transient filesystem results.
            # The pydantic-ai tree alone is 81 MB of text, so a model that
            # reads broadly could trip it.
            memory_usage_limit=512 * 1024 * 1024,
        )
        self._open_session()
        return self

    def __exit__(self, *exc) -> None:
        self._close_session()
        if self._mount is not None:
            self._mount.close()
        if self._pool is not None:
            self._pool.__exit__(*exc)

    def _open_session(self) -> None:
        self._session = self._pool.checkout(
            script_name="repo_explorer.py",
            limits={
                "max_duration_secs": SESSION_DURATION_BUDGET,
                "max_memory": SESSION_MEMORY_BUDGET,
            },
        )
        self._session.__enter__()
        self._poisoned = False

    def _close_session(self) -> None:
        if self._session is not None:
            try:
                self._session.__exit__(None, None, None)
            except Exception:
                pass
            self._session = None

    def _reset_session(self) -> None:
        self._close_session()
        self._open_session()

    # ---- the tool body ----------------------------------------------------
    def _feed(self, code: str, streams: CollectStreams):
        kwargs = {"mount": self._mount, "print_callback": streams}
        if self.prelude:
            kwargs["external_lookup"] = self.helpers.as_external_lookup()
        return self._session.feed_run(code, **kwargs)

    def run_python(self, code: str) -> str:
        n = self.stats.n_calls + 1
        streams = CollectStreams()
        t0 = time.time()
        was_reset = False
        error_class: str | None = None
        ok = True

        try:
            value = self._feed(code, streams)
        except (MontyRuntimeError, MontySyntaxError) as e:
            ok = False
            error_class = type(e).__name__
            body = e.display("traceback")
            # A poisoned session fails instantly with the SAME limit error for
            # every subsequent feed -- detect and rebuild so the run can go on.
            short = e.display("type-msg")
            if short.startswith(("TimeoutError: time limit exceeded",
                                 "MemoryError: memory limit exceeded")):
                self._reset_session()
                was_reset = True
                body += (
                    "\n\n[sandbox notice] This session exceeded its resource "
                    "budget and has been RESET. All variables and function "
                    "definitions from earlier run_python calls are gone -- "
                    "redefine anything you still need. Avoid unbounded loops "
                    "and reading very large files."
                )
            value = None
            out = self._format(streams, body, value_repr=None)
        except MontyTypingError as e:
            ok = False
            error_class = "MontyTypingError"
            out = self._format(streams, e.display(), value_repr=None)
        except MontyCrashedError as e:
            ok = False
            error_class = "MontyCrashedError"
            self._reset_session()
            was_reset = True
            out = self._format(
                streams,
                f"SandboxCrashed: the worker died (timed_out={e.timed_out}). "
                f"The session has been RESET -- all previous variables are gone.",
                value_repr=None,
            )
        except MontyError as e:
            ok = False
            error_class = type(e).__name__
            out = self._format(streams, f"{type(e).__name__}: {e}", value_repr=None)
        else:
            out = self._format(
                streams, None, value_repr=None if value is None else repr(value)
            )

        elapsed = time.time() - t0
        truncated = out.endswith("[truncated]")
        self.stats.calls.append(
            CallRecord(
                n=n, code=code, output=out, ok=ok, error_class=error_class,
                elapsed=round(elapsed, 3), truncated=truncated,
                session_reset=was_reset,
            )
        )
        return out

    @staticmethod
    def _format(streams: CollectStreams, error: str | None, value_repr: str | None) -> str:
        stdout = "".join(t for s, t in streams.output if s == "stdout")
        stderr = "".join(t for s, t in streams.output if s == "stderr")
        parts: list[str] = []
        if stdout:
            parts.append(stdout.rstrip("\n"))
        if stderr:
            parts.append(f"[stderr]\n{stderr.rstrip()}")
        if error:
            parts.append(f"[error]\n{error.rstrip()}")
        elif value_repr is not None:
            parts.append(f"[value] {value_repr}")
        if not parts:
            parts.append("[no output] (the snippet printed nothing and its last "
                         "line was not an expression)")
        out = "\n".join(parts)
        if len(out) > MAX_TOOL_OUTPUT:
            out = out[:MAX_TOOL_OUTPUT] + "\n...[truncated]"
        return out
