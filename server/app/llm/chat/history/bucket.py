"""What a `messages.bucket` holds, and how a turn's dump is prepared for it.

The bucket is the JSON column that carries everything a row needs beyond
its display `content`: the pydantic-ai ModelMessage dump of an assistant
turn (replayed to the model on later turns), the exact prompt a user row's
model saw, the client's message id, and the interrupted/error markers of an
unfinished turn. The raw bucket never goes over the wire.
"""

from __future__ import annotations

from typing import Any, Optional

from app.database.models import Message

BUCKET_DUMP_KEY = "pai_messages"
BUCKET_VERSION_KEY = "pai_v"
BUCKET_VERSION = 1
# On user rows: the exact prompt text sent to the model when it differs
# from the display content (e.g. the reference-citation block is appended).
MODEL_PROMPT_KEY = "model_prompt"
# On assistant rows: the turn ended without a completed answer (client
# disconnect, user stop, or a mid-run provider failure). `BUCKET_ERROR_KEY`
# is present only in the failure case: `{"message": "..."}`.
BUCKET_INTERRUPTED_KEY = "interrupted"
BUCKET_ERROR_KEY = "error"
# On user rows: the AI SDK message id the client sent. History serves it as
# the UIMessage id so reloaded turns dedupe against the live ones.
CLIENT_MESSAGE_ID_KEY = "client_message_id"


def dump_from_bucket(message: Message) -> Optional[Any]:
    bucket = getattr(message, "bucket", None) or {}
    if not isinstance(bucket, dict):
        return None
    return bucket.get(BUCKET_DUMP_KEY)


def strip_instructions(dump: Any) -> Any:
    """Null out `instructions` on dumped ModelRequests before save.

    Every ModelRequest in a turn carries the full system prompt (which in
    `full` context mode embeds the whole paper) — 70%+ of the dump's bytes,
    repeated per request. It is never read back on replay: pydantic-ai
    re-injects the *current* run's instructions, so persisting them only
    bloats the row and burns the model-history char budget.
    """
    if isinstance(dump, list):
        for entry in dump:
            if isinstance(entry, dict) and entry.get("kind") == "request":
                entry["instructions"] = None
    return dump
