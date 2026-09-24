"""The stream pump: one agent run -> the encoded UI-message stream.

`StreamPump` drives pydantic-ai's Vercel adapter (`transform_stream` over
the run's native events, then `encode_stream`) in a TASK that feeds a
bounded queue, and the response iterates the queue. Paper chat and quick
question both stream through it.

Why a task and a queue rather than a plain `async for`: a provider retry
backoff (`RetryingModel`) sleeps INSIDE the agent run, i.e. inside the pull
side of the stream, and the `data-retry-status` chunks announcing it have to
reach the client DURING that wait. `push_retry_status` is the
`RetryingModel.on_retry` callback that injects them into the queue.

Teardown is the other half. `close()` runs a shielded, time-bounded
background teardown that stops the pump (which can swallow its own
cancellation — see `_stop_pump`), closes the native agent stream directly
(closing only the protocol generator leaves the provider HTTP stream running
and billing) and finally releases the per-request HTTP client.

Callers own the ordering around it:

    pump = StreamPump()
    model = RetryingModel(built, on_retry=pump.push_retry_status)
    ...
    try:
        pump.start(adapter, adapter.run_stream_native(...), on_complete=...)
        async for chunk in pump:
            yield chunk
        await pump.join()            # re-raise what the pump failed on
    finally:
        pump.cancel()                # synchronous: the pump cannot advance
        ...                          # synchronous persistence, if any
        await pump.close(model)      # shielded teardown
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from app.llm.chat.stream import OpenPaperAdapter
from app.llm.retrying_model import RetryStatus, retry_status_payload
from app.schemas.chat_stream import RETRY_STATUS_PART_TYPE

logger = logging.getLogger(__name__)

# Transient UI chunk announcing a provider retry. Wire contract with the
# client (camelCase payload keys):
#   {"type":"data-retry-status","transient":true,
#    "data":{"state":"retrying","attempt":2,"maxAttempts":3,
#            "delayMs":2000,"error":"..."}}
#   {"type":"data-retry-status","transient":true,"data":{"state":"recovered"}}
RETRY_STATUS_CHUNK_TYPE = RETRY_STATUS_PART_TYPE

# Sentinel pushed onto the chunk queue when the protocol stream is done.
_STREAM_END = object()

# Look-ahead the pump may build up before it blocks on the consumer. Small
# enough to keep the provider stream paced by the client, big enough that a
# burst of chunks doesn't ping-pong the event loop.
CHUNK_QUEUE_SIZE = 32

# How long teardown waits for a cancelled pump before re-cancelling it, and
# again before giving up. Cancellation can be swallowed by a `finally` that
# yields, so "cancel once and wait forever" is not safe (see `_stop_pump`).
PUMP_TEARDOWN_TIMEOUT = 5.0
# Attempts at stopping the pump; the last one closes the HTTP transport
# first, to unblock a pump waiting on the provider rather than on us.
_PUMP_STOP_ATTEMPTS = 3

# Hard ceiling on a background teardown. Comfortably above the pump budget
# (_PUMP_STOP_ATTEMPTS x PUMP_TEARDOWN_TIMEOUT), so it only ever fires for a
# close that has genuinely wedged.
TEARDOWN_DEADLINE_SECONDS = 45.0

# Strong references to in-flight (shielded) stream teardowns; without them
# the event loop can garbage-collect one mid-flight.
_BACKGROUND_TEARDOWNS: set = set()

# `on_complete` for `transform_stream`: runs after a successful run, and
# whatever it yields goes out before the finish chunks.
OnComplete = Callable[[Any], AsyncIterator[Any]]


class StreamPump:
    """One run's chunk queue, pump task and teardown. Single-use.

    `lookahead` is how many encoded chunks the run may get ahead of the
    reader (default `CHUNK_QUEUE_SIZE`). A pump can never be purely
    demand-driven, but a lookahead of 1 comes closest to a plain pull loop:
    a stalled or departed client then costs at most one chunk of extra work.
    """

    def __init__(self, *, lookahead: Optional[int] = None) -> None:
        # Bounded, so a client that stops reading still throttles the
        # provider stream the way a direct loop would instead of buffering a
        # whole turn. The bound is why iteration ALSO stops on a finished
        # pump: the end-of-stream sentinel is pushed from a `finally` that
        # runs under cancellation too, where it must never suspend and so
        # may be dropped.
        size = CHUNK_QUEUE_SIZE if lookahead is None else max(1, lookahead)
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=size)
        self._adapter: Optional[OpenPaperAdapter[Any, Any]] = None
        self._native_stream: Optional[Any] = None
        self._event_stream: Optional[Any] = None
        self._task: Optional[asyncio.Task[None]] = None

    async def push_retry_status(self, status: RetryStatus) -> None:
        """Push a transient `data-retry-status` chunk into the live stream."""
        chunk = DataChunk(
            type=RETRY_STATUS_CHUNK_TYPE,
            data=retry_status_payload(status),
            transient=True,
        )
        stream_state = self._adapter.last_event_stream if self._adapter else None
        encoded = (
            stream_state.encode_event(chunk)
            if stream_state is not None
            else f"data: {chunk.encode(6)}\n\n"
        )
        if status.state == "recovered":
            # "recovered" is the ONLY chunk that clears the client's retry
            # banner, so it must not be dropped — but it must not evict a
            # queued chunk either: on a later step the queue holds real
            # content, and losing a `text-start` or a tool lifecycle chunk
            # breaks protocol pairing on the client. So block, exactly like
            # the pump already does; a stalled reader throttling us is the
            # intended behavior, and teardown's drainer means this cannot
            # deadlock after a disconnect.
            #
            # A CancelledError here MUST escape: `RetryingModel._notify`
            # swallows only `Exception`, so teardown cancellation is never
            # eaten inside the retry loop.
            await self._queue.put(encoded)
            return
        try:
            # A missed "retrying" is cosmetic; never stall the run for it.
            self._queue.put_nowait(encoded)
        except asyncio.QueueFull:
            logger.debug("Dropped retry-status chunk: chunk queue is full")

    def start(
        self,
        adapter: OpenPaperAdapter[Any, Any],
        native_stream: AsyncIterator[Any],
        *,
        on_complete: Optional[OnComplete] = None,
    ) -> None:
        """Start pumping `native_stream` (from `adapter.run_stream_native`).

        The stream is composed by hand (rather than `adapter.run_stream`) so
        the NATIVE agent event stream can be closed directly on
        interruption. Closing only the outer protocol generator is not
        enough: pydantic-ai's transform_stream yields its finish chunks from
        a `finally`, which makes aclose() raise "async generator ignored
        GeneratorExit" and leaves the provider HTTP stream running (and
        billing) in the background.
        """
        self._adapter = adapter
        self._native_stream = native_stream
        self._event_stream = adapter.transform_stream(
            native_stream, on_complete=on_complete
        )
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        """Drive the protocol stream into the queue.

        A task, not an inline `async for`, so `push_retry_status` — which
        fires from inside the agent run, i.e. from THIS task — can hand a
        transient chunk to the consumer while a retry backoff sleeps.
        """
        assert self._adapter is not None and self._event_stream is not None
        try:
            async for encoded in self._adapter.encode_stream(self._event_stream):
                await self._queue.put(encoded)
        finally:
            # `put_nowait`, never `await put`: this also runs when the
            # pump is CANCELLED, where suspending would strand the
            # consumer forever. A full queue drops the sentinel, which
            # the consumer's `task.done()` check covers.
            try:
                self._queue.put_nowait(_STREAM_END)
            except asyncio.QueueFull:
                pass

    def __aiter__(self) -> "StreamPump":
        return self

    async def __anext__(self) -> str:
        task = self._task
        # Ends on the sentinel, or on a finished pump with nothing left
        # (the sentinel can be dropped when the queue is full).
        if task is None or (self._queue.empty() and task.done()):
            raise StopAsyncIteration
        item = await self._queue.get()
        if item is _STREAM_END:
            raise StopAsyncIteration
        return item

    async def join(self) -> None:
        """Re-raise anything the pump failed on (encode/transform errors)."""
        if self._task is not None:
            await self._task

    def cancel(self) -> None:
        """Stop the pump. Synchronous, so everything a caller does before its
        next `await` runs with the pump unable to advance."""
        if self._task is not None:
            self._task.cancel()

    async def close(self, model: Optional[Any]) -> None:
        """Tear the run down: pump, agent streams, then `model`'s transport.

        SHIELDED: teardown must finish even when the caller is being torn
        down by task cancellation (server shutdown, anyio disconnect). anyio
        re-delivers cancellation at every await, so awaiting the closes
        inline would abandon them — leaving the provider HTTP stream open
        and billing, and racing `aclose()` against a pump that is still
        iterating the same generator ("already running").
        """
        teardown = _schedule_teardown(
            _close_stream_stack(
                self._task,
                self._queue,
                self._native_stream,
                self._event_stream,
                model,
            )
        )
        if teardown is not None:
            try:
                await asyncio.shield(teardown)
            except BaseException:
                # Cancelled out from under us — `teardown` keeps running to
                # completion on its own (the registry holds the reference).
                pass


def _teardown_finished(task: "asyncio.Task") -> None:
    """Retire a background teardown, surfacing whatever it ended with.

    Retrieving the result matters: a task whose exception is never read logs
    "Task exception was never retrieved" at GC time, from a context with no
    connection to the request that produced it.
    """
    _BACKGROUND_TEARDOWNS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Chat stream teardown did not finish cleanly: %s", exc)


def _schedule_teardown(coro: Any) -> Optional["asyncio.Task"]:
    """Run `coro` as a tracked, time-bounded background task.

    The deadline is the backstop for a close that never returns (a wedged
    provider socket): without it a task would sit in `_BACKGROUND_TEARDOWNS`
    forever and they would pile up across disconnects.
    """
    try:
        task = asyncio.ensure_future(asyncio.wait_for(coro, TEARDOWN_DEADLINE_SECONDS))
    except RuntimeError as exc:  # pragma: no cover - loop already gone
        # Nothing left to schedule on (interpreter/loop shutdown). Do not
        # mask whatever exception is propagating through the caller's
        # `finally`.
        logger.warning("Could not schedule chat stream teardown: %s", exc)
        coro.close()
        return None
    _BACKGROUND_TEARDOWNS.add(task)
    task.add_done_callback(_teardown_finished)
    return task


async def _stop_pump(
    pump_task: asyncio.Task,
    chunk_queue: asyncio.Queue,
    *,
    escalate: Optional[Callable[[], Awaitable[None]]] = None,
) -> bool:
    """Wait out an already-cancelled pump without letting it block.

    Never raises. Returns True when the pump is finished, False when it had
    to be ABANDONED — the caller must then treat the streams as still owned
    by it (see `_close_stream_stack`).

    Two hazards, both real:

    1. `transform_stream` yields its finish chunks from a `finally`
       (pydantic-ai `ui/_event_stream.py`), so a `CancelledError` delivered
       while the pump sits in `__anext__` is CONSUMED by that yield: the
       generator hands back a chunk, `__anext__` returns normally, and the
       pump keeps running. Cancellation is edge-triggered, so it does not
       fire again by itself.
    2. The pump then blocks on `queue.put()` — the consumer is gone, so
       nobody will ever make room, and teardown would hang forever (the
       response task never completes and its DB session is never returned).

    So: drain concurrently for the WHOLE sequence (the pump can never block
    on the queue), re-cancel on each timeout, and before the final attempt
    run `escalate` — closing the HTTP transport, which unblocks a pump stuck
    on provider I/O rather than on us.
    """

    async def drain() -> None:
        while True:
            await chunk_queue.get()

    drainer = asyncio.ensure_future(drain())
    try:
        for attempt in range(_PUMP_STOP_ATTEMPTS):
            if attempt == _PUMP_STOP_ATTEMPTS - 1 and escalate is not None:
                try:
                    await escalate()
                except BaseException as exc:  # pragma: no cover - defensive
                    logger.warning("Teardown escalation failed: %s", exc)
            try:
                await asyncio.wait_for(asyncio.shield(pump_task), PUMP_TEARDOWN_TIMEOUT)
                return True
            except asyncio.CancelledError:
                # Ambiguous: either the pump finished (cancelled, which the
                # shield reports as CancelledError) or THIS teardown task is
                # being cancelled. Only `pump_task` can tell them apart.
                if pump_task.done():
                    return True
                pump_task.cancel()
            except asyncio.TimeoutError:
                # A `finally`-yield swallowed the last cancel: re-arm it.
                pump_task.cancel()
            except Exception:
                # The pump raised — it is finished either way.
                return True
        if pump_task.done():
            return True
        logger.error(
            "Chat pump did not stop within %ss; abandoning it",
            _PUMP_STOP_ATTEMPTS * PUMP_TEARDOWN_TIMEOUT,
        )
        return False
    finally:
        drainer.cancel()
        try:
            await drainer
        except BaseException:
            # Cancelled, as intended — awaiting just keeps the loop from
            # collecting it while still pending.
            pass


async def _close_stream_stack(
    pump_task: Optional[asyncio.Task],
    chunk_queue: asyncio.Queue,
    native_stream: Optional[Any],
    event_stream: Optional[Any],
    model: Optional[Any] = None,
) -> None:
    """Stop the pump, close the agent streams, release the client.

    Never raises. Order matters: the pump owns the iteration of both
    generators, so it has to be finished before either is closed — and the
    HTTP client outlives both, so it goes last.
    """
    from app.llm._pai_compat import close_model_transport

    transport_closed = False

    async def close_transport() -> None:
        # Idempotent: used both as the escalation lever and as the last step.
        nonlocal transport_closed
        if transport_closed:
            return
        transport_closed = True
        await close_model_transport(model)

    if pump_task is not None:
        stopped = await _stop_pump(pump_task, chunk_queue, escalate=close_transport)
        if not stopped:
            # The pump may still be inside `__anext__` on these very
            # generators. Closing one under active iteration raises
            # "asynchronous generator is already running" and can corrupt
            # the run's teardown — worse than leaking them to the garbage
            # collector, which is what we do instead. The transport is
            # already closed (the escalation above), so nothing is left
            # streaming or billing.
            logger.error(
                "Skipping stream close: chat pump still running (streams left to GC)"
            )
            return
    # Both may be None when the failure happened before they were built.
    try:
        if native_stream is not None:
            await native_stream.aclose()
    except BaseException as exc:
        logger.warning("Failed to close native agent stream: %s", exc)
    try:
        if event_stream is not None:
            await event_stream.aclose()
    except BaseException:
        # transform_stream yields finish chunks from its `finally`;
        # aclose() reports that as RuntimeError. The native stream is
        # already closed above, so nothing is leaked.
        pass
    # The client is built per request and the pydantic-ai provider does not
    # own it, so nothing else would ever close it.
    await close_transport()
