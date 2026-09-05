"""OpenPaper's protocol layer over the Vercel AI UIMessage stream.

`OpenPaperEventStream` extends pydantic-ai's `VercelAIEventStream` with two
behaviors:

1. **Evidence holdback.** The agent ends its answer with a
   `---EVIDENCE---...---END-EVIDENCE---` block that must never reach the
   client as visible text (parsed citations are delivered separately as a
   `data-citations` chunk). Text is filtered through a state machine that:
   - withholds any suffix which could be the start of a delimiter, so a
     marker split across deltas never leaks;
   - suppresses everything between the START and END markers, then resumes
     (a model that emits the block mid-message doesn't lose its answer);
   - emits `text-start` lazily on the first visible character, so a part
     that is *only* evidence produces no chunks at all — and `text-end` is
     only emitted for parts that were started (the AI SDK client hard-fails
     on an end without a start);
   - flushes an unfinished non-delimiter tail before closing a text part.

2. **Tool output truncation.** Full tool returns (whole paper sections)
   don't belong on the wire; oversized outputs are replaced with a preview
   marker. The same cap is applied by the history serializer so live and
   reloaded turns match.

The stream also accumulates the raw (unfiltered) text so the caller can
persist a partial answer when the run is interrupted, and `on_error` stashes
`error_text` (with `error_context` for the log). That stash is the only
record of a mid-run failure: pydantic-ai's `transform_stream` swallows the
exception into an error chunk on a 200 response, so without it the runtime
could not tell a user stop from a failure — which is exactly what decides
whether the persisted row gets an error and whether the client offers a
retry.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional

from pydantic_ai.messages import FunctionToolResultEvent, TextPart, TextPartDelta
from pydantic_ai.output import OutputDataT
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.ui import UIEventStream
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.request_types import RequestData
from pydantic_ai.ui.vercel_ai.response_types import (
    BaseChunk,
    TextDeltaChunk,
    TextEndChunk,
    TextStartChunk,
    ToolOutputAvailableChunk,
)

from app.llm.chat.citations import EVIDENCE_END, EVIDENCE_START

logger = logging.getLogger(__name__)

# Tool outputs larger than this (JSON-serialized) are replaced by a preview
# over the wire and in history serialization. The model still sees the full
# output — this cap is UI-only.
TOOL_OUTPUT_WIRE_CAP = 6000

# Cap on the stashed failure text (it is persisted on the failed turn's row
# and served back to the client as message metadata).
MAX_ERROR_TEXT_CHARS = 2000


def truncate_tool_output(output: Any, cap: int = TOOL_OUTPUT_WIRE_CAP) -> Any:
    """Cap a tool output for UI transport.

    Structure is preserved for small outputs; oversized ones become a
    `{truncated: true, preview: "..."}` marker so the client can render a
    peek without shipping whole paper sections.
    """
    try:
        serialized = json.dumps(output, default=str)
    except (TypeError, ValueError):
        serialized = str(output)
    if len(serialized) <= cap:
        return output
    return {"truncated": True, "preview": serialized[:cap]}


def _held_suffix_len(buf: str, marker: str) -> int:
    """Length of the longest suffix of `buf` that is a proper prefix of
    `marker` (i.e. could still grow into it)."""
    max_check = min(len(buf), len(marker) - 1)
    for length in range(max_check, 0, -1):
        if marker.startswith(buf[-length:]):
            return length
    return 0


_CITE_PREFIX = "@cite["
# A complete `@cite[n|...]` marker line — the fallback evidence trigger for
# models that mangle the opening `---EVIDENCE---` marker (observed live).
_CITE_LINE_RE = re.compile(r"@cite\[\d+[^\]\n]*\][ \t\r]*")
# Give up holding an unterminated `@cite[` line after this many chars — a
# real marker line is short; anything longer is prose.
_MAX_CITE_LINE = 200


class EvidenceFilter:
    """Incremental filter that removes evidence blocks from streamed text.

    Semantics match `citations.strip_evidence_blocks` applied to the full
    text: everything from `---EVIDENCE---` — or from a bare full-line
    `@cite[n|...]` marker (fallback for mangled openers) — up to
    `---END-EVIDENCE---` is removed; an unterminated block extends to the
    end. The one divergence from the batch stripper: a mangled textual
    header (e.g. `**EVIDENCE**`) preceding a fallback-detected block has
    already streamed by the time the trigger fires, so only persistence
    gets it trimmed.
    """

    def __init__(self) -> None:
        self.in_evidence = False
        self._tail = ""
        # Char immediately preceding the current tail/buffer; stream start
        # counts as a line start.
        self._prev_char = "\n"

    def _line_start_at(self, buf: str, i: int) -> bool:
        return buf[i - 1] == "\n" if i > 0 else self._prev_char == "\n"

    def _find_cite_trigger(self, buf: str) -> tuple[Optional[int], Optional[int]]:
        """Scan for the fallback trigger.

        Returns (validated_index, pending_index): `validated_index` is the
        earliest line-start `@cite[...]` line proven complete inside `buf`;
        `pending_index` is a line-start `@cite[`-prefix whose line hasn't
        terminated yet (must be held, not emitted).
        """
        search = 0
        while True:
            i = buf.find(_CITE_PREFIX, search)
            if i == -1:
                return None, None
            if not self._line_start_at(buf, i):
                search = i + 1
                continue
            nl = buf.find("\n", i)
            if nl == -1:
                if len(buf) - i > _MAX_CITE_LINE:
                    return None, None  # too long — prose, not a marker
                return None, i
            if _CITE_LINE_RE.fullmatch(buf[i:nl].rstrip("\r")) is not None:
                return i, None
            search = nl + 1

    def push(self, content: str) -> str:
        out: list[str] = []
        buf = self._tail + content
        self._tail = ""

        def emit(segment: str) -> None:
            if segment:
                out.append(segment)
                self._prev_char = segment[-1]

        while buf:
            if not self.in_evidence:
                marker_idx = buf.find(EVIDENCE_START)
                cite_idx, pending_idx = self._find_cite_trigger(buf)

                triggers = [
                    t
                    for t in (
                        (marker_idx, marker_idx + len(EVIDENCE_START))
                        if marker_idx != -1
                        else None,
                        (cite_idx, cite_idx) if cite_idx is not None else None,
                    )
                    if t is not None
                ]
                trigger = min(triggers) if triggers else None

                if trigger is not None and (
                    pending_idx is None or pending_idx > trigger[0]
                ):
                    cut, consume_from = trigger
                    emit(buf[:cut])
                    if consume_from > cut:
                        self._prev_char = buf[consume_from - 1]
                    buf = buf[consume_from:]
                    self.in_evidence = True
                    continue

                if pending_idx is not None:
                    # A possible marker line is still incomplete — hold it.
                    emit(buf[:pending_idx])
                    self._tail = buf[pending_idx:]
                    buf = ""
                    continue

                keep = _held_suffix_len(buf, EVIDENCE_START)
                for length in range(min(len(buf), len(_CITE_PREFIX) - 1), keep, -1):
                    if _CITE_PREFIX.startswith(
                        buf[-length:]
                    ) and self._line_start_at(buf, len(buf) - length):
                        keep = length
                        break
                if keep:
                    emit(buf[:-keep])
                    self._tail = buf[-keep:]
                else:
                    emit(buf)
                buf = ""
            else:
                idx = buf.find(EVIDENCE_END)
                if idx != -1:
                    end = idx + len(EVIDENCE_END)
                    self._prev_char = buf[end - 1]
                    buf = buf[end:]
                    self.in_evidence = False
                    continue
                keep = _held_suffix_len(buf, EVIDENCE_END)
                # Evidence content itself is discarded (citations are parsed
                # from the accumulated raw text after the run).
                self._tail = buf[-keep:] if keep else ""
                buf = ""
        return "".join(out)

    def flush(self) -> str:
        """Return any held-back tail that never became a delimiter.

        A held tail that IS a complete `@cite[...]` marker line at end of
        stream (no trailing newline) still counts as evidence and is
        suppressed, matching the batch stripper's `$`-anchored match.
        """
        if self.in_evidence:
            self._tail = ""
            return ""
        tail, self._tail = self._tail, ""
        if tail.startswith(_CITE_PREFIX) and self._prev_char == "\n":
            rest = tail
            if _CITE_LINE_RE.fullmatch(rest.rstrip("\r")) is not None:
                self.in_evidence = True
                return ""
        return tail


@dataclass
class OpenPaperEventStream(VercelAIEventStream[AgentDepsT, OutputDataT]):
    """Vercel AI event stream with evidence holdback + tool output caps."""

    accumulated_text: str = ""
    # Set by `on_error`: the run died mid-stream (as opposed to the user
    # stopping it). The runtime reads this to persist the failed turn with
    # its error and to report the real message in telemetry.
    error_text: Optional[str] = None
    # Identifying facts for the failure log (model, conversation, paper).
    error_context: Dict[str, Any] = field(default_factory=dict)
    _part_open: bool = False

    def __post_init__(self) -> None:
        self._evidence_filter = EvidenceFilter()

    def _text_chunks(self, emit: str) -> list[BaseChunk]:
        """Emit visible text, opening the part lazily on first content."""
        chunks: list[BaseChunk] = []
        if not emit:
            return chunks
        if not self._part_open:
            self._part_open = True
            chunks.append(TextStartChunk(id=self.new_message_id()))
        chunks.append(TextDeltaChunk(id=self.message_id, delta=emit))
        return chunks

    async def handle_text_start(
        self, part: TextPart, follows_text: bool = False
    ) -> AsyncIterator[BaseChunk]:
        content = part.content or ""
        # Separate distinct text parts (e.g. text before and after a tool
        # step) so persisted content doesn't run words together.
        if not follows_text and self.accumulated_text and content:
            self.accumulated_text += "\n\n"
        self.accumulated_text += content
        # `follows_text` continuations reuse the open part (if any); a fresh
        # part after reasoning/tools starts closed and opens lazily.
        for chunk in self._text_chunks(self._evidence_filter.push(content)):
            yield chunk

    async def handle_text_delta(self, delta: TextPartDelta) -> AsyncIterator[BaseChunk]:
        content = delta.content_delta or ""
        self.accumulated_text += content
        for chunk in self._text_chunks(self._evidence_filter.push(content)):
            yield chunk

    async def handle_text_end(
        self, part: TextPart, followed_by_text: bool = False
    ) -> AsyncIterator[BaseChunk]:
        if followed_by_text:
            # The next part visually continues this one: keep holdback state
            # (a delimiter may complete across the boundary), keep the part
            # open.
            return
        for chunk in self._text_chunks(self._evidence_filter.flush()):
            yield chunk
        if self._part_open:
            self._part_open = False
            yield TextEndChunk(id=self.message_id)

    async def on_error(self, error: Exception) -> AsyncIterator[BaseChunk]:
        # pydantic-ai swallows the exception here (it becomes an error chunk
        # on a 200 response), so this is the ONLY place it can be logged —
        # and the only place the runtime can learn what actually failed.
        text = str(error).strip() or type(error).__name__
        self.error_text = text[:MAX_ERROR_TEXT_CHARS]
        context = (
            ", ".join(f"{k}={v}" for k, v in sorted(self.error_context.items()))
            or "no context"
        )
        logger.exception(
            "Chat stream failed [%s]: %s: %s",
            context,
            type(error).__name__,
            text,
            exc_info=error,
        )
        # Close a dangling text part first — the AI SDK client would
        # otherwise leave it in `streaming` state forever.
        for chunk in self._text_chunks(self._evidence_filter.flush()):
            yield chunk
        if self._part_open:
            self._part_open = False
            yield TextEndChunk(id=self.message_id)
        async for chunk in super().on_error(error):
            yield chunk

    async def handle_function_tool_result(
        self, event: FunctionToolResultEvent
    ) -> AsyncIterator[BaseChunk]:
        async for chunk in super().handle_function_tool_result(event):
            if isinstance(chunk, ToolOutputAvailableChunk):
                chunk = ToolOutputAvailableChunk(
                    tool_call_id=chunk.tool_call_id,
                    output=truncate_tool_output(chunk.output),
                    provider_executed=chunk.provider_executed,
                )
            yield chunk


@dataclass
class OpenPaperAdapter(VercelAIAdapter[AgentDepsT, OutputDataT]):
    """VercelAIAdapter that builds the OpenPaper event stream.

    Keeps a handle on the built stream so the endpoint can read
    `accumulated_text` for partial-answer persistence on interruption.
    The base adapter calls `build_event_stream()` twice per run (once for
    transformation, once for encoding), so the stream is memoized — which
    also makes each adapter instance SINGLE-USE: a second run would reuse
    dirty filter/accumulator state. Build a fresh adapter per request.
    """

    last_event_stream: Optional[OpenPaperEventStream] = None
    # Forwarded to the event stream so a mid-run failure logs WHICH chat
    # (model / conversation / paper) died.
    error_context: Optional[Dict[str, Any]] = None

    def build_event_stream(self) -> UIEventStream[RequestData, BaseChunk, AgentDepsT, OutputDataT]:
        # `VercelAIAdapter` calls this once from `transform_stream` (the
        # instance that actually sees the run's events) and AGAIN from
        # `encode_stream` (which only needs `encode_event`). Memoize so
        # `last_event_stream` keeps pointing at the transforming instance —
        # otherwise `accumulated_text` is always empty at `on_complete`.
        if self.last_event_stream is not None:
            return self.last_event_stream
        stream = OpenPaperEventStream(
            self.run_input,
            accept=self.accept,
            sdk_version=self.sdk_version,
            server_message_id=self.server_message_id,
            error_context=dict(self.error_context or {}),
        )
        self.last_event_stream = stream
        return stream
