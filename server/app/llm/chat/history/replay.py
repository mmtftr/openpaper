"""Model history: which stored turns the next agent run sees, and how.

A newest-first WINDOW of turns replays its persisted `bucket.pai_messages`
dump verbatim (tool calls + returns + final text), sanitized for the target
model (`sanitize.replay_sanitizer`); everything older either renders as
plain text or is dropped. NEVER paginated — pagination is a UI concern.

The window's oldest edge is quantized to a fixed "grid" so that the
request's byte PREFIX is identical from turn to turn — that is what provider
prompt caches (OpenAI/Azure automatic prefix caching) hash. See
`load_model_history` for the full rationale.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from app.database.models import Message
from app.llm.chat.history.bucket import MODEL_PROMPT_KEY, dump_from_bucket
from app.llm.chat.history.figures import figure_replay_cost
from app.llm.chat.history.sanitize import replay_sanitizer

logger = logging.getLogger(__name__)

# High-water mark (chars) for the full-dump replay window: ~90k tokens at
# 4 chars/token. The window is the newest run of turns that fits under it.
MODEL_HISTORY_REPLAY_CHARS = 360_000
# Quantum by which the window's OLDEST edge is allowed to move. Chosen so a
# jump evicts ~1/3 of the window: rarer cache invalidations than a per-turn
# boundary, at the cost of one uncached re-read of the window when it fires.
MODEL_HISTORY_REPLAY_CHUNK = 120_000
# Same pair for the plain-TEXT tail ahead of the window. The window decides
# how much DETAIL recent turns keep; this bounds how far back the thread
# reaches at all, so a months-long conversation cannot outgrow the model's
# context window and fail every later turn. Rows past it are dropped.
# Setting it to 0 turns the tail off entirely (whole-turn eviction).
MODEL_HISTORY_TEXT_CHARS = 120_000
MODEL_HISTORY_TEXT_CHUNK = 40_000
# Always sent, whatever the caps: the newest turn must survive.
MIN_HISTORY_ROWS = 2


def _text_cost(message: Message) -> int:
    """Chars a row contributes when it renders as plain text."""
    return len(str(getattr(message, "content", "") or ""))


def _replay_costs(
    rows: Sequence[Message], *, supports_vision: bool
) -> tuple[List[int], List[bool]]:
    """Per-row `(cost, replayable)` in row order.

    Cost is what the row adds to the request as it will actually be sent: an
    assistant row with a usable dump costs its serialized size PLUS the
    base64 figure bytes rehydration splices back in (figures are persisted
    with `data=""`, so `json.dumps` alone hides them — and a couple of them
    would silently blow the window and get the request rejected on context
    length). Charged only for a vision model, since a vision-less one gets a
    short placeholder instead. Every other row costs its text length.

    A dump that will not serialize is charged — and later rendered — as the
    text row it degrades to, never skipped: an uncharged row would make the
    cost grid disagree with what is actually sent.
    """
    figure_sizes: Dict[str, int] = {}
    costs: List[int] = []
    replayable: List[bool] = []
    for message in rows:
        dump = (
            dump_from_bucket(message)
            if str(getattr(message, "role", "")) == "assistant"
            else None
        )
        cost: Optional[int] = None
        if dump is not None:
            try:
                cost = len(json.dumps(dump))
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "Unserializable pai_messages on message %s (%s); "
                    "charging it as a text row",
                    getattr(message, "id", "?"),
                    exc,
                )
        if cost is None:
            costs.append(_text_cost(message))
            replayable.append(False)
            continue
        if supports_vision:
            cost += figure_replay_cost(dump, figure_sizes)
        costs.append(cost)
        replayable.append(True)
    return costs, replayable


def _prefix_sums(costs: Sequence[int]) -> List[int]:
    """`out[i]` = total cost of rows `[0, i)`; `len(out) == len(costs) + 1`."""
    out = [0]
    for cost in costs:
        out.append(out[-1] + cost)
    return out


def _grid_lines(costs: Sequence[int], is_user: Sequence[bool], chunk: int) -> List[int]:
    """Fixed boundary candidates for a replay window, oldest first.

    Grid line 0 is the start of the conversation. Grid line k (k >= 1) is
    the FIRST user row whose cumulative cost-before is >= `k * chunk`.
    Because the cost of a past row never changes, these thresholds pin the
    lines to absolute positions: appending turns can only ADD lines at the
    end, never move the existing ones. That is what makes the window's
    oldest edge — and therefore the request's byte prefix — stable.

    Only user rows qualify, so a window never opens on an answer whose
    question was evicted.
    """
    lines = [0]
    if chunk <= 0:
        # Degenerate (test/config): every user row is a boundary.
        lines.extend(index for index in range(1, len(costs)) if is_user[index])
        return lines
    cumulative = 0
    threshold = chunk
    for index, cost in enumerate(costs):
        if index and is_user[index] and cumulative >= threshold:
            lines.append(index)
            # One huge turn can cross several thresholds at once; skip them
            # so a single index is not minted as several grid lines.
            threshold += chunk * (1 + (cumulative - threshold) // chunk)
        cumulative += cost
    return lines


def _pick_start(lines: Sequence[int], prefix: Sequence[int], end: int, cap: int) -> int:
    """Oldest grid line whose cost through `end` (exclusive) fits `cap`.

    `end` itself is the last resort, so `cap = 0` degenerates to an EMPTY
    range rather than an over-cap one — that is what makes `TEXT_CHARS = 0`
    mean "whole-turn eviction, no text tail".
    """
    for line in lines:
        if line >= end:
            break
        if prefix[end] - prefix[line] <= cap:
            return line
    return end


def load_model_history(
    rows: Sequence[Message],
    *,
    spec: Optional[Any] = None,
    replay_chars: int = MODEL_HISTORY_REPLAY_CHARS,
    replay_chunk: int = MODEL_HISTORY_REPLAY_CHUNK,
    text_chars: int = MODEL_HISTORY_TEXT_CHARS,
    text_chunk: int = MODEL_HISTORY_TEXT_CHUNK,
) -> List[ModelMessage]:
    """Build the ModelMessage history for the next agent turn.

    `rows` must be the FULL conversation in chronological order. The result
    has three zones, oldest to newest:

    1. **dropped** — nothing is sent;
    2. **text tail** — plain user/assistant text from the `content` column;
    3. **replay window** — turns replayed from their `pai_messages` dump
       (tool calls, returns, thinking, final text). Rows inside the window
       without a usable dump still render as plain text.

    Why the zones move in JUMPS
    ---------------------------
    Providers cache prompt prefixes by hashing the leading BYTES of the
    request (OpenAI/Azure automatic prefix caching). Any rewrite of an older
    part of the history — even one that shrinks it — invalidates the whole
    cached prefix. The previous policy walked the history newest-first and
    spent a budget row by row, so every single turn nudged the boundary and
    every first request of a turn missed the cache (measured: 29% hit rate
    on gpt-5.5, with three consecutive same-model turns at 0%).

    So the boundaries are not "wherever the budget runs out" but the nearest
    line of a fixed GRID (`_grid_lines`) anchored at absolute cumulative
    cost. Appending turns leaves the grid untouched, so the window start —
    and every byte before the new turn — is bit-identical from turn to turn
    until cumulative cost crosses the next `replay_chunk` multiple. The cost
    of a jump is exactly one uncached re-read of the window; between jumps
    every turn hits the cache.

    Sizing: the window starts at the OLDEST grid line whose cost through the
    newest row fits `replay_chars`, so it holds between `replay_chars -
    replay_chunk` and `replay_chars` worth of turns. The text tail is bounded
    the same way over text cost with `text_chars` / `text_chunk`; setting
    `text_chars = 0` disables the tail entirely (pure whole-turn eviction).

    Invariants, whatever the caps say: the newest `MIN_HISTORY_ROWS` rows are
    always sent, and the history opens on a user row.

    `spec` is the CURRENT turn's `ModelSpec` (anything exposing
    `supports_vision` / `api`), NOT the capability of whichever model
    produced the history — see `replay_sanitizer`. Passing `None` keeps the
    pre-capability behavior (rehydrate everything, touch nothing).
    """
    prepare_dump, supports_vision = replay_sanitizer(spec)
    total = len(rows)
    if total == 0:
        return []

    is_user = [str(getattr(row, "role", "")) == "user" for row in rows]
    costs, replayable = _replay_costs(rows, supports_vision=supports_vision)
    window_start = _pick_start(
        _grid_lines(costs, is_user, replay_chunk),
        _prefix_sums(costs),
        total,
        replay_chars,
    )
    # `window_start == total` means even the newest turn is over the cap; it
    # then degrades to text like any evicted turn (and the MIN_HISTORY_ROWS
    # clamp below still keeps it in the request).
    text_costs = [_text_cost(row) for row in rows]
    keep_from = _pick_start(
        _grid_lines(text_costs, is_user, text_chunk),
        _prefix_sums(text_costs),
        window_start,
        text_chars,
    )
    # The newest turn is never dropped, however far over the caps it is.
    keep_from = min(keep_from, max(0, total - MIN_HISTORY_ROWS))
    # Never open the history on an answer without its question. Grid lines
    # are user rows already; this only fixes line 0 of a conversation whose
    # first row is not a user row.
    while keep_from < total - MIN_HISTORY_ROWS and not is_user[keep_from]:
        keep_from += 1

    history: List[ModelMessage] = []
    pending_user_text: Optional[str] = None

    def _flush_pending() -> None:
        nonlocal pending_user_text
        if pending_user_text is not None:
            history.append(
                ModelRequest(parts=[UserPromptPart(content=pending_user_text)])
            )
            pending_user_text = None

    for index in range(keep_from, total):
        message = rows[index]
        dump = dump_from_bucket(message)
        if index >= window_start and replayable[index] and dump is not None:
            try:
                hydrated = prepare_dump(copy.deepcopy(dump))
                # Round-trip through JSON so base64 `data` fields decode back
                # to bytes — `validate_python` rejects the base64 string.
                turn_messages = ModelMessagesTypeAdapter.validate_json(
                    json.dumps(hydrated)
                )
                # The runtime feeds the user prompt to the agent as trailing
                # message_history, so `new_messages()` — and therefore the
                # dump — does NOT contain it (verified against pydantic-ai
                # 1.89.1). Flush the DB user row ahead of the responses,
                # unless this particular dump does carry its own leading
                # user request (defensive against format changes).
                if _dump_leads_with_user_prompt(turn_messages):
                    pending_user_text = None
                else:
                    _flush_pending()
                history.extend(turn_messages)
                continue
            except Exception as exc:
                logger.warning(
                    "Failed to deserialize pai_messages on message %s: %s",
                    getattr(message, "id", "?"),
                    exc,
                )
                # Fall through to text fallback for this turn.

        content = str(getattr(message, "content", "") or "")
        if not content:
            continue
        if message.role == "user":
            _flush_pending()
            # Prefer the exact prompt the model saw (includes the user's
            # reference-citation block) when the runtime stored it.
            bucket = getattr(message, "bucket", None) or {}
            prompt = bucket.get(MODEL_PROMPT_KEY) if isinstance(bucket, dict) else None
            pending_user_text = str(prompt) if prompt else content
        elif message.role == "assistant":
            _flush_pending()
            history.append(ModelResponse(parts=[TextPart(content=content)]))

    _flush_pending()
    return history


def _dump_leads_with_user_prompt(messages: List[ModelMessage]) -> bool:
    if not messages:
        return False
    first = messages[0]
    if not isinstance(first, ModelRequest):
        return False
    return any(isinstance(part, UserPromptPart) for part in first.parts)
