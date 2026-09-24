"""End-to-end tests for the `run_paper_chat` SSE generator.

These drive the real runtime — model wrapping, the pump task / queue that
decouples chunk delivery from the retry backoff, `on_complete` persistence,
the error path and the partial-turn safety net — against a scripted model.
Everything with I/O (DB, paper context, telemetry, quota) is monkeypatched;
what is exercised is the streaming machinery and what comes out on the wire.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.wrapper import WrapperModel

from app.database.models import ConversableType, SubscriptionPlan
from app.llm.chat import runtime as runtime_module
from app.llm.chat.runtime import (
    CHUNK_QUEUE_SIZE,
    RETRY_STATUS_CHUNK_TYPE,
    ChatRequestError,
    run_paper_chat,
)
from app.llm._pai_compat import MODEL_TRANSPORT_CLOSER
from app.llm.chat.stream import OpenPaperAdapter
from app.llm.provider import LLMProvider
from app.llm.model_registry import ModelSpec
from app.llm.retrying_model import RetryingModel

PAPER_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())
ANSWER = "hello from the model"


# =====================================================================
# scripted model
# =====================================================================


class FlakyStreamModel(WrapperModel):
    """Fails `failures` times while ENTERING the stream, then delegates."""

    def __init__(self, wrapped, *, failures: int, status: int = 500) -> None:
        super().__init__(wrapped)
        self.remaining = failures
        self.status = status
        self.entries = 0

    @asynccontextmanager
    async def request_stream(
        self, messages, model_settings, model_request_parameters, run_context=None
    ):
        self.entries += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise ModelHTTPError(
                status_code=self.status, model_name="scripted", body=None
            )
        async with self.wrapped.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response:
            yield response


# =====================================================================
# fakes
# =====================================================================


class FakeMessageCrud:
    def __init__(self, rows: Optional[List[Any]] = None) -> None:
        self.rows = list(rows or [])
        self.created: List[Any] = []
        self.updated: List[Any] = []
        self.removed: List[Any] = []

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
        self.created.append(row)
        return row

    def update(self, db, *, db_obj, obj_in, user):
        self.updated.append((db_obj, obj_in))
        return db_obj

    def remove(self, db, *, id, user=None):
        self.removed.append(id)
        for index, row in enumerate(self.rows):
            if row.id == id:
                return self.rows.pop(index)
        return None


def _run_input(text: str = "why does this work?", message_id: str = "client-1"):
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
    return OpenPaperAdapter.build_run_input(json.dumps(payload).encode("utf-8"))


class _Fixture:
    """Everything `run_paper_chat` needs, wired to fakes."""

    def __init__(
        self,
        monkeypatch,
        *,
        model,
        rows: Optional[List[Any]] = None,
        gate_sleep: bool = False,
    ):
        self.monkeypatch = monkeypatch
        self.model = model
        # When set, retry backoffs block until the test releases this gate,
        # so a test can prove chunks escape DURING the wait. `sleep_started`
        # is the matching synchronization point — tests wait on it instead
        # of sleeping and hoping.
        self.sleep_gate = asyncio.Event() if gate_sleep else None
        self.sleep_started = asyncio.Event()
        self.crud = FakeMessageCrud(rows)
        self.events: List[tuple] = []
        self.user = SimpleNamespace(id=uuid.uuid4())
        self.db = SimpleNamespace(rollback=lambda: None)
        self.spec = ModelSpec(
            id="scripted-model",
            provider=LLMProvider.OPENAI,
            display_name="Scripted",
        )
        self.built_models: List[Any] = []
        self._install()

    def _install(self):
        mp = self.monkeypatch
        fixture = self

        paper = SimpleNamespace(id=PAPER_ID, title="A paper")
        context = SimpleNamespace(
            paper=paper,
            context_mode="adaptive",
            system_prompt="be helpful",
            supplementary_papers=[],
            allowed_paper_ids=[PAPER_ID],
            family_index={},
            repo_snapshot=None,
        )
        conversation = SimpleNamespace(
            id=CONVERSATION_ID,
            conversable_type=ConversableType.PAPER,
            conversable_id=PAPER_ID,
        )

        class FakeRegistry:
            def resolve(self, provider=None, model_id=None, role=None):
                return fixture.spec

            def build_model(self, spec):
                fixture.built_models.append(fixture.model)
                return fixture.model

            def build_settings(self, spec, reasoning_effort=None, **kwargs):
                # `**kwargs` so a new keyword on the real signature (e.g.
                # `cache_key`) doesn't turn every runtime test into a
                # TypeError.
                return None

        def fake_build_agent(*, model, spec, system_prompt, paper, context_mode,
                             repo_snapshot):
            fixture.agent_model = model
            return Agent(model, output_type=str, instructions=system_prompt)

        gate = self.sleep_gate
        started = self.sleep_started

        def fake_retrying(model, *, on_retry=None):
            async def sleep(delay: float) -> None:
                started.set()
                if gate is not None:
                    await gate.wait()

            return RetryingModel(model, on_retry=on_retry, sleep=sleep)

        mp.setattr(runtime_module, "get_registry", lambda: FakeRegistry())
        mp.setattr(runtime_module, "build_paper_agent", fake_build_agent)
        mp.setattr(
            runtime_module, "build_paper_chat_context", lambda *a, **k: context
        )
        mp.setattr(
            runtime_module.conversation_crud, "get", lambda *a, **k: conversation
        )
        mp.setattr(runtime_module, "message_crud", self.crud)
        # The partial-turn safety net opens its own session. `get` is the
        # id-keyed idempotency probe: no row exists in these tests.
        mp.setattr(
            runtime_module,
            "SessionLocal",
            lambda: SimpleNamespace(
                close=lambda: None, get=lambda model, row_id: None
            ),
        )
        mp.setattr(
            runtime_module,
            "track_event",
            lambda name, **kwargs: self.events.append((name, kwargs)),
        )
        # Fast, deterministic backoff — the schedule itself is unit-tested.
        mp.setattr(runtime_module, "RetryingModel", fake_retrying)

        import app.helpers.subscription_limits as limits

        mp.setattr(limits, "can_user_chat", lambda db, user: (True, None))
        mp.setattr(
            limits,
            "get_user_subscription_plan",
            lambda db, user: SubscriptionPlan.BASIC,
        )

        import app.llm.operations as operations_module

        mp.setattr(
            operations_module.operations,
            "rename_conversation",
            lambda **kwargs: None,
        )

    def stream(self, *, text: str = "why does this work?", message_id: str = "client-1"):
        return run_paper_chat(
            db=self.db,
            current_user=self.user,
            run_input=_run_input(text, message_id),
            accept=None,
            paper_id=PAPER_ID,
            conversation_id=CONVERSATION_ID,
            provider=None,
            model=None,
            reasoning_effort=None,
            context_mode=None,
            user_references=None,
        )

    def collect(self, **kwargs) -> List[Dict[str, Any]]:
        async def drive():
            out = []
            async for encoded in self.stream(**kwargs):
                out.append(encoded)
            return out

        return _parse(asyncio.run(drive()))


def _parse(encoded: Sequence[str]) -> List[Dict[str, Any]]:
    chunks = []
    for raw in encoded:
        assert raw.startswith("data: "), raw
        assert raw.endswith("\n\n"), raw
        body = raw[len("data: ") : -2]
        if body == "[DONE]":
            chunks.append({"type": "[DONE]"})
            continue
        chunks.append(json.loads(body))
    return chunks


def _of_type(chunks: Sequence[Dict[str, Any]], type_: str) -> List[Dict[str, Any]]:
    return [c for c in chunks if c.get("type") == type_]


# =====================================================================
# tests
# =====================================================================


class TestRetryStatusOnTheWire:
    def test_transient_failures_emit_retry_then_recovered(self, monkeypatch):
        model = FlakyStreamModel(
            TestModel(custom_output_text=ANSWER, call_tools=[]), failures=2
        )
        fixture = _Fixture(monkeypatch, model=model)
        chunks = fixture.collect()

        retry_chunks = _of_type(chunks, RETRY_STATUS_CHUNK_TYPE)
        assert len(retry_chunks) == 3, chunks
        first, second, recovered = retry_chunks

        # EXACT wire contract (key order is irrelevant, content is not).
        assert first == {
            "type": "data-retry-status",
            "transient": True,
            "data": {
                "state": "retrying",
                "attempt": 2,
                "maxAttempts": 3,
                "delayMs": 1000,
                "error": first["data"]["error"],
            },
        }
        assert "500" in first["data"]["error"]
        assert second["data"]["attempt"] == 3
        assert second["data"]["delayMs"] == 2000
        assert recovered == {
            "type": "data-retry-status",
            "transient": True,
            "data": {"state": "recovered"},
        }
        # 3 entries: two failures + the successful one.
        assert model.entries == 3

    def test_retry_status_precedes_the_answer(self, monkeypatch):
        model = FlakyStreamModel(
            TestModel(custom_output_text=ANSWER, call_tools=[]), failures=1
        )
        fixture = _Fixture(monkeypatch, model=model)
        chunks = fixture.collect()

        types = [c.get("type") for c in chunks]
        assert types.index(RETRY_STATUS_CHUNK_TYPE) < types.index("text-delta")
        text = "".join(c["delta"] for c in _of_type(chunks, "text-delta"))
        assert text == ANSWER
        assert types[-1] == "[DONE]"
        # The answer was persisted exactly once, as a completed turn.
        assistant = [r for r in fixture.crud.created if r.role == "assistant"]
        assert len(assistant) == 1
        assert assistant[0].content == ANSWER
        assert "interrupted" not in (assistant[0].bucket or {})

    def test_chunk_escapes_while_the_backoff_is_still_sleeping(self, monkeypatch):
        """The whole reason for the pump task + queue: the status chunk must
        reach the client DURING the backoff, not after it. With the backoff
        parked on a gate, a buffered implementation cannot produce it."""
        model = FlakyStreamModel(
            TestModel(custom_output_text=ANSWER, call_tools=[]), failures=1
        )
        fixture = _Fixture(monkeypatch, model=model, gate_sleep=True)
        assert fixture.sleep_gate is not None

        async def drive():
            stream = fixture.stream()
            try:
                retry_chunk = None
                while retry_chunk is None:
                    chunk = await asyncio.wait_for(stream.__anext__(), timeout=2.0)
                    if RETRY_STATUS_CHUNK_TYPE in chunk:
                        retry_chunk = chunk
                # Still inside the backoff: the model has been entered once.
                assert model.entries == 1
                fixture.sleep_gate.set()
                rest = [chunk async for chunk in stream]
                return retry_chunk, rest
            finally:
                fixture.sleep_gate.set()
                await stream.aclose()

        retry_chunk, rest = asyncio.run(drive())
        assert _parse([retry_chunk])[0]["data"]["state"] == "retrying"
        tail = _parse(rest)
        assert "".join(c["delta"] for c in _of_type(tail, "text-delta")) == ANSWER
        assert [c["data"]["state"] for c in _of_type(tail, RETRY_STATUS_CHUNK_TYPE)] == [
            "recovered"
        ]
        assert model.entries == 2

    def test_happy_path_emits_no_retry_chunks(self, monkeypatch):
        model = TestModel(custom_output_text=ANSWER, call_tools=[])
        fixture = _Fixture(monkeypatch, model=model)
        chunks = fixture.collect()
        assert _of_type(chunks, RETRY_STATUS_CHUNK_TYPE) == []
        assert _of_type(chunks, "error") == []


class TestFailedTurn:
    def _run(self, monkeypatch, status: int = 500):
        model = FlakyStreamModel(
            TestModel(custom_output_text=ANSWER, call_tools=[]),
            failures=99,
            status=status,
        )
        fixture = _Fixture(monkeypatch, model=model)
        return fixture, fixture.collect()

    def test_exhausted_retries_produce_an_error_chunk(self, monkeypatch):
        fixture, chunks = self._run(monkeypatch)

        errors = _of_type(chunks, "error")
        assert len(errors) == 1
        assert "500" in json.dumps(errors[0])
        finish = _of_type(chunks, "finish")
        assert finish and finish[0]["finishReason"] == "error"
        # Two retries were announced, and never a recovery.
        states = [
            c["data"]["state"] for c in _of_type(chunks, RETRY_STATUS_CHUNK_TYPE)
        ]
        assert states == ["retrying", "retrying"]

    def test_error_is_persisted_on_an_empty_assistant_row(self, monkeypatch):
        fixture, _ = self._run(monkeypatch)

        assistant = [r for r in fixture.crud.created if r.role == "assistant"]
        assert len(assistant) == 1, "the failed turn must leave a row behind"
        row = assistant[0]
        assert row.content == ""
        assert row.bucket["interrupted"] is True
        assert row.bucket["client_message_id"] == "client-1"
        assert "500" in row.bucket["error"]["message"]

    def test_failed_row_lands_before_the_error_chunk(self, monkeypatch):
        """A client that retries the instant it sees the error must find the
        failed row already in history, or the retry duplicates the turn."""
        model = FlakyStreamModel(
            TestModel(custom_output_text=ANSWER, call_tools=[]), failures=99
        )
        fixture = _Fixture(monkeypatch, model=model)

        async def drive():
            stream = fixture.stream()
            try:
                async for encoded in stream:
                    if '"type":"error"' in encoded:
                        return [
                            r for r in fixture.crud.created if r.role == "assistant"
                        ]
            finally:
                await stream.aclose()
            return None

        persisted = asyncio.run(drive())
        assert persisted, "assistant row not persisted before the error chunk"
        assert persisted[0].bucket["interrupted"] is True
        assert "error" in persisted[0].bucket

    def test_telemetry_carries_the_real_error(self, monkeypatch):
        fixture, _ = self._run(monkeypatch)
        errors = [e for e in fixture.events if e[0] == "chat_message_error"]
        assert len(errors) == 1
        properties = errors[0][1]["properties"]
        assert properties["error"] != "stream_error"
        assert "500" in properties["error"]
        assert properties["model"] == "scripted-model"

    def test_non_retryable_failure_is_not_retried(self, monkeypatch):
        fixture, chunks = self._run(monkeypatch, status=400)
        assert fixture.model.entries == 1
        assert _of_type(chunks, RETRY_STATUS_CHUNK_TYPE) == []
        assert _of_type(chunks, "error")


class TestInterruption:
    def test_disconnect_persists_the_partial_turn(self, monkeypatch):
        """Closing the generator mid-stream (the endpoint's disconnect path)
        must still run the safety net and tear the pump task down."""
        model = TestModel(custom_output_text=ANSWER, call_tools=[])
        fixture = _Fixture(monkeypatch, model=model)

        async def drive():
            stream = fixture.stream()
            seen = []
            async for encoded in stream:
                seen.append(encoded)
                if "text-delta" in encoded:
                    break
            await stream.aclose()
            # Nothing may outlive aclose(): a surviving pump task would keep
            # consuming (and billing) the provider stream. The agent's own
            # tasks are cancelled by the native-stream close and need a tick
            # to unwind.
            await asyncio.sleep(0.05)
            leftovers = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task() and not task.done()
            ]
            return seen, leftovers, set(runtime_module._BACKGROUND_TEARDOWNS)

        seen, leftovers, tracked = asyncio.run(drive())
        assert any("text-delta" in s for s in seen)
        assert leftovers == []
        assert tracked == set(), "background teardown was not retired"

        assistant = [r for r in fixture.crud.created if r.role == "assistant"]
        assert len(assistant) == 1
        row = assistant[0]
        assert row.bucket["interrupted"] is True
        # A user stop is NOT an error: no error payload on the row.
        assert "error" not in row.bucket
        assert row.content and ANSWER.startswith(row.content)


class TestRetryResubmission:
    def _rows(self, *, question: str, interrupted_bucket: Optional[dict]):
        rows = [
            SimpleNamespace(
                id=uuid.uuid4(),
                role="user",
                content=question,
                references=None,
                bucket={"client_message_id": "client-0"},
            )
        ]
        if interrupted_bucket is not None:
            rows.append(
                SimpleNamespace(
                    id=uuid.uuid4(),
                    role="assistant",
                    content="",
                    references=None,
                    bucket=interrupted_bucket,
                )
            )
        return rows

    def test_failed_turn_is_replaced_not_appended(self, monkeypatch):
        question = "why does this work?"
        rows = self._rows(
            question=question,
            interrupted_bucket={
                "client_message_id": "client-0",
                "interrupted": True,
                "error": {"message": "boom"},
            },
        )
        model = TestModel(custom_output_text=ANSWER, call_tools=[])
        fixture = _Fixture(monkeypatch, model=model, rows=rows)
        fixture.collect(text=question, message_id="client-1")

        # The failed assistant row was deleted, the question row reused.
        assert fixture.crud.removed == [rows[1].id]
        assert [db_obj for db_obj, _ in fixture.crud.updated] == [rows[0]]
        assert [r.role for r in fixture.crud.created] == ["assistant"]
        # Final history: one question, one (new, successful) answer.
        final = fixture.crud.rows + fixture.crud.created
        assert [r.role for r in final] == ["user", "assistant"]
        assert final[1].content == ANSWER
        assert "interrupted" not in (final[1].bucket or {})

    def test_stopped_turn_with_text_is_preserved(self, monkeypatch):
        """A stop/disconnect row that holds a real answer must survive a
        resubmission of the same question — only failures are replaced."""
        question = "why does this work?"
        rows = self._rows(
            question=question,
            interrupted_bucket={"client_message_id": "client-0", "interrupted": True},
        )
        rows[1].content = "a complete answer that was never finalized"
        model = TestModel(custom_output_text=ANSWER, call_tools=[])
        fixture = _Fixture(monkeypatch, model=model, rows=rows)
        fixture.collect(text=question, message_id="client-1")

        assert fixture.crud.removed == []
        assert fixture.crud.updated == []
        assert [r.role for r in fixture.crud.created] == ["user", "assistant"]

    def test_dangling_user_row_still_reused(self, monkeypatch):
        question = "why does this work?"
        rows = self._rows(question=question, interrupted_bucket=None)
        model = TestModel(custom_output_text=ANSWER, call_tools=[])
        fixture = _Fixture(monkeypatch, model=model, rows=rows)
        fixture.collect(text=question, message_id="client-1")

        assert fixture.crud.removed == []
        assert [db_obj for db_obj, _ in fixture.crud.updated] == [rows[0]]
        assert [r.role for r in fixture.crud.created] == ["assistant"]


class TestPersistenceResilience:
    """The safety net is called twice on the error path (before the error
    chunk, then from the `finally`). A transient DB failure on the first
    call must not latch it as done, or the turn is lost entirely."""

    def _failing_crud(self, fixture, *, fail_first: int):
        crud = fixture.crud
        original = crud.create
        state = {"attempts": 0}

        def flaky_create(db, *, obj_in, user):
            if obj_in.role == "assistant":
                state["attempts"] += 1
                if state["attempts"] <= fail_first:
                    raise RuntimeError("connection reset by peer")
            return original(db, obj_in=obj_in, user=user)

        crud.create = flaky_create  # type: ignore[method-assign]
        return state

    def test_transient_failure_is_retried_by_the_finally(self, monkeypatch):
        model = FlakyStreamModel(
            TestModel(custom_output_text=ANSWER, call_tools=[]), failures=99
        )
        fixture = _Fixture(monkeypatch, model=model)
        attempts = self._failing_crud(fixture, fail_first=1)
        fixture.collect()

        assert attempts["attempts"] == 2, "the failed write was not retried"
        rows = [r for r in fixture.crud.created if r.role == "assistant"]
        assert len(rows) == 1
        assert rows[0].bucket["interrupted"] is True
        assert "error" in rows[0].bucket

    def test_completed_answer_is_never_relabelled_as_interrupted(self, monkeypatch):
        """If `on_complete`'s INSERT fails, the net must re-persist the
        COMPLETE answer — not stamp a correct answer as a failed turn."""
        fixture = _Fixture(
            monkeypatch, model=TestModel(custom_output_text=ANSWER, call_tools=[])
        )
        attempts = self._failing_crud(fixture, fail_first=1)
        fixture.collect()

        assert attempts["attempts"] == 2
        rows = [r for r in fixture.crud.created if r.role == "assistant"]
        assert len(rows) == 1
        assert rows[0].content == ANSWER
        assert "interrupted" not in rows[0].bucket
        assert "error" not in rows[0].bucket

    def test_existing_row_short_circuits_the_write(self, monkeypatch):
        """Idempotent by the preallocated id: an ambiguous commit that
        actually landed must not produce a second row."""
        model = FlakyStreamModel(
            TestModel(custom_output_text=ANSWER, call_tools=[]), failures=99
        )
        fixture = _Fixture(monkeypatch, model=model)
        monkeypatch.setattr(
            runtime_module,
            "SessionLocal",
            lambda: SimpleNamespace(
                close=lambda: None,
                get=lambda model_cls, row_id: SimpleNamespace(id=row_id),
            ),
        )
        fixture.collect()
        assert [r for r in fixture.crud.created if r.role == "assistant"] == []


class TestReusedUserRowIsMerged:
    """A retry resends the text only. Overwriting the row would null the
    user's citations and drop the stored `model_prompt`."""

    def _rows(self):
        question = "why does this work?"
        user = SimpleNamespace(
            id=uuid.uuid4(),
            role="user",
            content=question,
            references={"citations": [{"key": 1, "reference": "a quote"}]},
            bucket={
                "client_message_id": "client-0",
                "model_prompt": f"{question}\n\nEVIDENCE BLOCK",
            },
        )
        failed = SimpleNamespace(
            id=uuid.uuid4(),
            role="assistant",
            content="",
            references=None,
            bucket={
                "client_message_id": "client-0",
                "interrupted": True,
                "error": {"message": "boom"},
            },
        )
        return question, [user, failed]

    def test_references_and_model_prompt_survive(self, monkeypatch):
        question, rows = self._rows()
        fixture = _Fixture(
            monkeypatch,
            model=TestModel(custom_output_text=ANSWER, call_tools=[]),
            rows=rows,
        )
        fixture.collect(text=question, message_id="client-1")

        assert len(fixture.crud.updated) == 1
        _, update = fixture.crud.updated[0]
        assert update.references == {
            "citations": [{"key": 1, "reference": "a quote"}]
        }
        assert update.bucket["model_prompt"] == f"{question}\n\nEVIDENCE BLOCK"
        # The new submission id still takes effect.
        assert update.bucket["client_message_id"] == "client-1"

    def test_text_only_retry_replays_the_stored_prompt(self, monkeypatch):
        """A text-only resubmission must ask the SAME question the original
        did: the stored `model_prompt` (with its evidence block) is what the
        model receives, not the bare text."""
        from contextlib import asynccontextmanager

        from pydantic_ai.messages import ModelRequest, UserPromptPart

        class _RecordingModel(WrapperModel):
            """Records every message list the model was asked to answer."""

            def __init__(self, wrapped):
                super().__init__(wrapped)
                self.requests: List[List[Any]] = []

            async def request(self, messages, *args, **kwargs):
                self.requests.append(list(messages))
                return await self.wrapped.request(messages, *args, **kwargs)

            @asynccontextmanager
            async def request_stream(self, messages, *args, **kwargs):
                self.requests.append(list(messages))
                async with self.wrapped.request_stream(
                    messages, *args, **kwargs
                ) as stream:
                    yield stream

        question, rows = self._rows()
        model = _RecordingModel(TestModel(custom_output_text=ANSWER, call_tools=[]))
        fixture = _Fixture(monkeypatch, model=model, rows=rows)
        fixture.collect(text=question, message_id="client-1")
        prompts = [
            str(part.content)
            for messages in model.requests
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        assert prompts, "the model was never called"
        assert any(question in p and "EVIDENCE BLOCK" in p for p in prompts)


class TestRetryStatusDelivery:
    """`recovered` is the only chunk that clears the client's retry banner,
    so it may not be dropped — and it may not evict a queued chunk either:
    losing a `text-start` or a tool lifecycle chunk breaks protocol pairing
    on the client. It blocks on a full queue instead."""

    def _recording_adapter(self, produced: List[str]):
        class RecordingAdapter(OpenPaperAdapter):
            def encode_stream(self_inner, stream):  # noqa: N805
                inner = super().encode_stream(stream)

                async def gen():
                    async for chunk in inner:
                        produced.append(chunk)
                        yield chunk

                return gen()

        return RecordingAdapter

    def test_recovered_never_drops_or_evicts_a_chunk(self, monkeypatch):
        produced: List[str] = []
        monkeypatch.setattr(runtime_module, "CHUNK_QUEUE_SIZE", 1)
        monkeypatch.setattr(
            runtime_module, "OpenPaperAdapter", self._recording_adapter(produced)
        )
        model = FlakyStreamModel(
            TestModel(custom_output_text=ANSWER, call_tools=[]), failures=1
        )
        fixture = _Fixture(monkeypatch, model=model, gate_sleep=True)
        assert fixture.sleep_gate is not None

        async def drive():
            stream = fixture.stream()
            # Take `start`, then stop reading: the single queue slot fills
            # with the "retrying" chunk while the backoff is parked, so the
            # "recovered" chunk lands on a FULL queue.
            first = await stream.__anext__()
            await asyncio.wait_for(fixture.sleep_started.wait(), timeout=2.0)
            fixture.sleep_gate.set()
            rest = [chunk async for chunk in stream]
            return [first, *rest]

        received = asyncio.run(drive())

        # Nothing lost, nothing reordered. Retry-status chunks are injected
        # straight into the queue (they never pass through `encode_stream`),
        # so compare the rest against everything the pump produced: an
        # implementation that evicts to make room shows up right here.
        content = [c for c in received if RETRY_STATUS_CHUNK_TYPE not in c]
        assert content == produced
        chunks = _parse(received)
        statuses = [
            c["data"]["state"] for c in _of_type(chunks, RETRY_STATUS_CHUNK_TYPE)
        ]
        assert statuses == ["retrying", "recovered"]
        assert "".join(c["delta"] for c in _of_type(chunks, "text-delta")) == ANSWER
        # Protocol pairing intact.
        types = [c.get("type") for c in chunks]
        assert types.count("text-start") == types.count("text-end") == 1
        assert types.index("text-start") < types.index("text-end")


class TestTransportLifecycle:
    """Chat clients are built per request and the pydantic-ai provider does
    not own them, so teardown has to close them or the sockets leak."""

    def _model_with_closer(self, closed: List[str]):
        model = TestModel(custom_output_text=ANSWER, call_tools=[])

        async def close() -> None:
            closed.append("closed")

        setattr(model, MODEL_TRANSPORT_CLOSER, close)
        return model

    def test_closed_after_a_completed_turn(self, monkeypatch):
        closed: List[str] = []
        fixture = _Fixture(monkeypatch, model=self._model_with_closer(closed))
        fixture.collect()
        assert closed == ["closed"]

    def test_closed_after_a_disconnect(self, monkeypatch):
        closed: List[str] = []
        fixture = _Fixture(monkeypatch, model=self._model_with_closer(closed))

        async def drive():
            stream = fixture.stream()
            async for chunk in stream:
                if "text-delta" in chunk:
                    break
            await stream.aclose()

        asyncio.run(drive())
        assert closed == ["closed"]


class TestPumpTeardown:
    """Teardown must never hang, even when the pump swallows its cancel.

    `transform_stream` yields its finish chunks from a `finally`, so a
    CancelledError delivered while the pump is inside `__anext__` is consumed
    by that yield — the pump resumes and blocks on a queue nobody drains any
    more. That wedges `aclose()`, and with it the response task and its DB
    session. `TestModel` never fills the queue, which is why the plain
    disconnect test cannot see this.
    """

    def _flooding_adapter(self, parked: asyncio.Event):
        chunk_count = CHUNK_QUEUE_SIZE + 1

        class FloodingAdapter(OpenPaperAdapter):
            def encode_stream(self_inner, stream):  # noqa: N805
                async def gen():
                    try:
                        for index in range(chunk_count):
                            yield (
                                'data: {"type":"text-delta","id":"x","delta":'
                                f'"{index}"}}\n\n'
                            )
                        # Suspended in `__anext__` with a full queue: exactly
                        # where a cancel gets swallowed below.
                        await parked.wait()
                    finally:
                        # What transform_stream does: emit finish chunks
                        # while unwinding.
                        for _ in range(4):
                            yield 'data: {"type":"finish"}\n\n'

                return gen()

        return FloodingAdapter

    def _immortal_adapter(self, swallow: int):
        """A pump that eats `swallow` cancellations before it will die."""

        class ImmortalAdapter(OpenPaperAdapter):
            def encode_stream(self_inner, stream):  # noqa: N805
                async def gen():
                    eaten = 0
                    while True:
                        try:
                            await asyncio.sleep(0.01)
                        except asyncio.CancelledError:
                            eaten += 1
                            if eaten > swallow:
                                raise
                        yield 'data: {"type":"text-delta","id":"x","delta":"."}\n\n'

                return gen()

        return ImmortalAdapter

    def test_unstoppable_pump_is_abandoned_within_the_budget(
        self, monkeypatch, caplog
    ):
        """When the pump cannot be stopped, teardown must still RETURN — and
        must not aclose() generators the pump may still be iterating."""
        closed: List[str] = []
        model = TestModel(custom_output_text=ANSWER, call_tools=[])

        async def close_transport() -> None:
            closed.append("closed")

        setattr(model, MODEL_TRANSPORT_CLOSER, close_transport)

        monkeypatch.setattr(runtime_module, "PUMP_TEARDOWN_TIMEOUT", 0.05)
        monkeypatch.setattr(
            runtime_module, "OpenPaperAdapter", self._immortal_adapter(swallow=4)
        )
        fixture = _Fixture(monkeypatch, model=model)

        async def drive():
            stream = fixture.stream()
            await stream.__anext__()
            started = asyncio.get_running_loop().time()
            await asyncio.wait_for(stream.aclose(), timeout=5.0)
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(0)  # let the done callback run
            return elapsed, set(runtime_module._BACKGROUND_TEARDOWNS)

        with caplog.at_level("ERROR", logger="app.llm.chat.runtime"):
            elapsed, tracked = asyncio.run(drive())

        # 3 attempts x 0.05s, plus slack — nowhere near the 5s wait_for.
        assert elapsed < 1.0, f"teardown took {elapsed:.2f}s"
        # The transport is closed even on the abandoned path: that is the
        # escalation that stops the provider streaming (and billing).
        assert closed == ["closed"]
        assert any(
            "Skipping stream close" in record.message for record in caplog.records
        ), "expected teardown to refuse to close streams under active iteration"
        assert tracked == set(), "background teardown was not retired"

    def test_aclose_returns_when_the_pump_swallows_its_cancel(self, monkeypatch):
        parked = asyncio.Event()
        fixture = _Fixture(
            monkeypatch, model=TestModel(custom_output_text=ANSWER, call_tools=[])
        )
        monkeypatch.setattr(
            runtime_module, "OpenPaperAdapter", self._flooding_adapter(parked)
        )

        async def drive():
            stream = fixture.stream()
            # Take exactly one chunk, then stop reading: the pump fills the
            # queue to its bound and parks in `__anext__`.
            first = await stream.__anext__()
            await asyncio.sleep(0.05)
            # Before the fix this never returns.
            await asyncio.wait_for(stream.aclose(), timeout=10.0)
            await asyncio.sleep(0.05)
            leftovers = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task() and not task.done()
            ]
            return first, leftovers, set(runtime_module._BACKGROUND_TEARDOWNS)

        first, leftovers, tracked = asyncio.run(drive())
        assert "text-delta" in first
        assert leftovers == []
        assert tracked == set(), "background teardown was not retired"


class TestVisionCapabilityReachesReplay:
    """The runtime must hand THIS turn's vision capability to the history
    replay: a figure fetched by an earlier vision-capable model otherwise
    gets rehydrated into the request and hard-400s a vision-less model
    (DeepSeek V4 Flash / Kimi K3) on every later turn of the conversation."""

    def _spy(self, monkeypatch, fixture) -> List[Dict[str, Any]]:
        calls: List[Dict[str, Any]] = []

        def fake_load(rows, *args, **kwargs):
            calls.append(kwargs)
            return []

        monkeypatch.setattr(runtime_module, "load_model_history", fake_load)
        return calls

    def _run(self, monkeypatch, *, supports_vision: bool):
        fixture = _Fixture(
            monkeypatch, model=TestModel(custom_output_text=ANSWER, call_tools=[])
        )
        fixture.spec = SimpleNamespace(
            id="FW-DeepSeek-V4-Flash-0731",
            provider=LLMProvider.OPENAI,
            display_name="DeepSeek",
            api="chat",
            supports_vision=supports_vision,
            supports_reasoning_effort=False,
            supports_reasoning_summaries=False,
        )
        calls = self._spy(monkeypatch, fixture)
        fixture.collect()
        return calls

    def test_non_vision_model_disables_rehydration(self, monkeypatch):
        calls = self._run(monkeypatch, supports_vision=False)
        assert len(calls) == 1
        assert calls[0]["spec"].supports_vision is False
        # The chat-completions api is what gates reasoning-id stripping.
        assert calls[0]["spec"].api == "chat"

    def test_vision_model_keeps_rehydration(self, monkeypatch):
        calls = self._run(monkeypatch, supports_vision=True)
        assert len(calls) == 1
        assert calls[0]["spec"].supports_vision is True


class TestDuplicateSubmission:
    """The 409 guard, and the one case the retry flow deliberately allows."""

    QUESTION = "why does this work?"

    def _completed_rows(self, client_id: str):
        return [
            SimpleNamespace(
                id=uuid.uuid4(),
                role="user",
                content=self.QUESTION,
                references=None,
                bucket={"client_message_id": client_id},
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                role="assistant",
                content="the original answer",
                references=None,
                bucket={"client_message_id": client_id},
            ),
        ]

    def test_resubmitting_a_completed_turn_is_rejected(self, monkeypatch):
        fixture = _Fixture(
            monkeypatch,
            model=TestModel(custom_output_text=ANSWER, call_tools=[]),
            rows=self._completed_rows("client-1"),
        )
        with pytest.raises(ChatRequestError) as excinfo:
            fixture.collect(text=self.QUESTION, message_id="client-1")
        assert excinfo.value.status_code == 409
        assert fixture.crud.created == []

    def test_a_new_message_id_is_accepted(self, monkeypatch):
        fixture = _Fixture(
            monkeypatch,
            model=TestModel(custom_output_text=ANSWER, call_tools=[]),
            rows=self._completed_rows("client-1"),
        )
        fixture.collect(text=self.QUESTION, message_id="client-2")
        assert [r.role for r in fixture.crud.created] == ["user", "assistant"]

    def test_same_id_after_a_failed_turn_is_allowed(self, monkeypatch):
        """Deliberate narrowing: the failed turn's rows are reused, so the
        client may retry with the id it already used."""
        rows = self._completed_rows("client-1")
        rows[1].content = ""
        rows[1].bucket = {
            "client_message_id": "client-1",
            "interrupted": True,
            "error": {"message": "boom"},
        }
        fixture = _Fixture(
            monkeypatch,
            model=TestModel(custom_output_text=ANSWER, call_tools=[]),
            rows=rows,
        )
        fixture.collect(text=self.QUESTION, message_id="client-1")
        assert fixture.crud.removed == [rows[1].id]
        assert [r.role for r in fixture.crud.created] == ["assistant"]
        final = fixture.crud.rows + fixture.crud.created
        assert [r.role for r in final] == ["user", "assistant"]
