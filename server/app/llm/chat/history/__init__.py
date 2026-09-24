"""Conversation history: two independent readers of the same `messages` rows.

- **The model** (`load_model_history`, `replay.py`): chronological
  ModelMessages for the next agent turn — a prefix-stable replay window of
  full `pai_messages` dumps, a plain-text tail ahead of it, and nothing
  before that. Replay is sanitized for the model about to be called
  (`sanitize.py`); figure bytes are dropped on save and rehydrated on replay
  (`figures.py`).
- **The client** (`serialize_ui_messages`, `ui.py`): Vercel AI UIMessage
  dicts with the same parts the live stream produced.

`bucket.py` names what a row's `bucket` column holds; the runtime's store
writes it, both readers read it.
"""

from app.llm.chat.history.bucket import (
    BUCKET_DUMP_KEY,
    BUCKET_ERROR_KEY,
    BUCKET_INTERRUPTED_KEY,
    BUCKET_VERSION,
    BUCKET_VERSION_KEY,
    CLIENT_MESSAGE_ID_KEY,
    MODEL_PROMPT_KEY,
    strip_instructions,
)
from app.llm.chat.history.figures import (
    FIGURE_ID_PREFIX,
    FIGURE_REPLAY_CHAR_ESTIMATE,
    rehydrate_figure_bytes,
    strip_figure_bytes,
)
from app.llm.chat.history.replay import MIN_HISTORY_ROWS, load_model_history
from app.llm.chat.history.sanitize import (
    IMAGE_PLACEHOLDER,
    strip_replayed_images,
    strip_responses_reasoning_ids,
)
from app.llm.chat.history.ui import serialize_ui_messages

__all__ = [
    "BUCKET_DUMP_KEY",
    "BUCKET_ERROR_KEY",
    "BUCKET_INTERRUPTED_KEY",
    "BUCKET_VERSION",
    "BUCKET_VERSION_KEY",
    "CLIENT_MESSAGE_ID_KEY",
    "FIGURE_ID_PREFIX",
    "FIGURE_REPLAY_CHAR_ESTIMATE",
    "IMAGE_PLACEHOLDER",
    "MIN_HISTORY_ROWS",
    "MODEL_PROMPT_KEY",
    "load_model_history",
    "rehydrate_figure_bytes",
    "serialize_ui_messages",
    "strip_figure_bytes",
    "strip_instructions",
    "strip_replayed_images",
    "strip_responses_reasoning_ids",
]
