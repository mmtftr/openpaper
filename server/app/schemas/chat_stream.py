"""OpenPaper's custom parts on the Vercel AI SDK UIMessage stream.

The chat and quick-question streams are the AI SDK v6 UIMessage protocol that
pydantic-ai's Vercel adapter emits. On top of it OpenPaper sends:

- `data-citations` (id `"citations"`): the turn's evidence citations. Sent
  twice on a live turn (raw, then reconciled against the PDF text; the same
  id makes the second replace the first) and rebuilt from the stored
  `messages.references` when history is loaded.
- `data-retry-status` (transient, never persisted): a provider call is being
  retried in place, or has recovered.
- message `metadata` on a failed or interrupted assistant turn in history.

The server builds these payloads through the models below, and
`GET /api/message/stream-parts` puts them in the OpenAPI schema. Field names
are the wire contract; the camelCase ones are camelCase on the wire.
"""

from typing import Annotated, Any, Literal, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, Field, RootModel

CITATIONS_PART_TYPE = "data-citations"
CITATIONS_PART_ID = "citations"
RETRY_STATUS_PART_TYPE = "data-retry-status"


class ChatCitation(BaseModel):
    """One `@cite[...]` entry from the answer's evidence block.

    PDF citations carry `page` (and `paper_id` when the quote came from a
    supplementary or other paper); code citations carry `file` plus the line
    range, `verified` and a SHA-pinned `github_url` instead. Keys that were
    never set are absent from the JSON; `github_url`/`start_line`/`end_line`
    can be an explicit null.
    """

    key: int
    reference: str
    page: Optional[int] = None
    paper_id: Optional[str] = None
    # How the quote was rewritten to match the PDF text or repo:
    # "normalizer" | "llm" for PDF quotes, "exact" | "repaired" | ... for code.
    matched_via: Optional[str] = None
    file: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    github_url: Optional[str] = None
    verified: Optional[bool] = None


class CitationsData(BaseModel):
    citations: list[ChatCitation]


class RetryStatusData(BaseModel):
    """`retrying` carries the attempt counters (and `error` when known);
    `recovered` is just the state."""

    state: Literal["retrying", "recovered"]
    attempt: Optional[int] = None
    maxAttempts: Optional[int] = None
    delayMs: Optional[int] = None
    error: Optional[str] = None


class CitationsDataPart(BaseModel):
    type: Literal["data-citations"]
    id: Literal["citations"]
    data: CitationsData


class RetryStatusDataPart(BaseModel):
    type: Literal["data-retry-status"]
    transient: Literal[True]
    data: RetryStatusData


class OpenPaperDataPart(
    RootModel[
        Annotated[
            Union[CitationsDataPart, RetryStatusDataPart],
            Field(discriminator="type"),
        ]
    ]
):
    """Every custom `data-*` part/chunk OpenPaper puts on the chat stream."""


class ChatMessageMetadata(BaseModel):
    """UIMessage `metadata` of an assistant turn that never completed:
    `interrupted` alone for a stop/disconnect, plus `errorText` when the run
    failed."""

    interrupted: Optional[bool] = None
    errorText: Optional[str] = None


class ChatStreamSchema(BaseModel):
    """Schema-only container that makes the stream's custom types appear in
    the OpenAPI document. Never actually served."""

    data_part: OpenPaperDataPart
    message_metadata: ChatMessageMetadata


# -- builders: the only place these payloads are assembled --------------------


def citations_data(citations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`data-citations` payload. Validates every citation; keys absent from a
    citation stay absent, explicit nulls stay null."""
    return CitationsData.model_validate({"citations": list(citations)}).model_dump(
        mode="json", exclude_unset=True
    )


def retry_status_data(
    state: Literal["retrying", "recovered"],
    *,
    attempt: Optional[int] = None,
    max_attempts: Optional[int] = None,
    delay_ms: Optional[int] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    """`data-retry-status` payload: `{"state": "recovered"}`, or the counters
    for `retrying` (as given, nulls included) plus `error` when non-empty."""
    fields: dict[str, Any] = {"state": state}
    if state == "retrying":
        fields.update(attempt=attempt, maxAttempts=max_attempts, delayMs=delay_ms)
        if error:
            fields["error"] = error
    return RetryStatusData(**fields).model_dump(mode="json", exclude_unset=True)


def message_metadata(
    *, interrupted: bool, error_text: Optional[str] = None
) -> dict[str, Any]:
    """Assistant-message `metadata` for an incomplete turn."""
    return ChatMessageMetadata(
        interrupted=interrupted, errorText=error_text or None
    ).model_dump(mode="json", exclude_none=True)
