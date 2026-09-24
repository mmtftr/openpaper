"""Structure-preserving previews of tool payloads for the chat UI.

The wire cap on tool outputs (`stream.TOOL_OUTPUT_WIRE_CAP`) used to be met
by slicing the JSON encoding at N characters, so the client received a string
that was neither the value nor valid JSON. `preview_json` trims the VALUE
instead: long strings are clipped, long collections keep their first entries,
nesting past a depth limit is elided — each with an inline marker saying what
was dropped — and the result is still a JSON value the client can
pretty-print and syntax-highlight.

The client mirrors these markers (`client/src/lib/jsonPreview.ts`) for its own
display clipping, so a preview reads the same whether the server or the
browser did the trimming. Keep the two in sync.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

# Budgets tried in order — (max chars per string, max entries per collection,
# max nesting depth) — until the serialized preview fits the cap. The last one
# yields at most a few hundred bytes, so any cap in the thousands is met.
PREVIEW_BUDGETS: Tuple[Tuple[int, int, int], ...] = (
    (1200, 40, 8),
    (600, 25, 8),
    (300, 15, 6),
    (160, 10, 5),
    (80, 6, 4),
    (40, 4, 3),
    (24, 3, 2),
)

# Key holding the "more keys" marker inside a clipped object.
MORE_KEY = "…"


def clip_string(text: str, cap: int) -> str:
    """`text` cut to `cap` chars with a tail saying how much was dropped."""
    if len(text) <= cap:
        return text
    return text[:cap] + f"…[+{len(text) - cap:,} chars]"


def clip_value(
    value: Any,
    *,
    string_cap: int,
    item_cap: int,
    max_depth: int,
    depth: int = 0,
) -> Any:
    """A copy of `value` trimmed to the given budget; always JSON-encodable.

    Non-JSON leaves (datetimes, models, anything `json.dumps` would need a
    `default` for) preview as their `str()` form. Nothing is mutated.
    """
    if isinstance(value, str):
        return clip_string(value, string_cap)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        if depth >= max_depth:
            return f"{{…{len(value):,} keys}}"
        clipped: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= item_cap:
                clipped[MORE_KEY] = f"[+{len(value) - item_cap:,} more keys]"
                break
            clipped[str(key)] = clip_value(
                item,
                string_cap=string_cap,
                item_cap=item_cap,
                max_depth=max_depth,
                depth=depth + 1,
            )
        return clipped
    if isinstance(value, (list, tuple)):
        if depth >= max_depth:
            return f"[…{len(value):,} items]"
        items: List[Any] = [
            clip_value(
                item,
                string_cap=string_cap,
                item_cap=item_cap,
                max_depth=max_depth,
                depth=depth + 1,
            )
            for item in value[:item_cap]
        ]
        if len(value) > item_cap:
            items.append(f"…[+{len(value) - item_cap:,} more items]")
        return items
    return clip_string(str(value), string_cap)


def preview_json(value: Any, cap: int) -> Optional[Any]:
    """The most generous budget's preview of `value` that serializes to at
    most `cap` chars, or None if even the tightest budget overflows."""
    for string_cap, item_cap, max_depth in PREVIEW_BUDGETS:
        candidate = clip_value(
            value, string_cap=string_cap, item_cap=item_cap, max_depth=max_depth
        )
        if len(json.dumps(candidate)) <= cap:
            return candidate
    return None
