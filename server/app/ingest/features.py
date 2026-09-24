"""Which paper features are usable, from the stage statuses (design §8).

`features(stages)` is pure: pass a paper's stage rows (or just their
statuses) keyed by stage name. For each feature it says whether it is
enabled and, if not, which stages it waits on, which stage is the actual
cause (the one to show / retry — possibly further upstream) and a readable
reason. The client renders "retrying in N s" itself from the cause stage's
`next_attempt_at`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol, Union

from app.ingest import graph
from app.ingest.models import IngestStage, StageStatus

# Feature -> stages that must all be succeeded/skipped.
FEATURES: dict[str, tuple[str, ...]] = {
    "reading": ("source",),
    "manual_highlights": ("source",),
    "notes": ("source",),
    # `metadata` skips `metadata_fallback` as soon as it resolves a record,
    # so this is ready after the lookup when it succeeds, else after the
    # fallback.
    "metadata": ("metadata", "metadata_fallback"),  # title / authors / cite
    "thumbnail": ("preview",),
    "chat": ("ocr_repair",),
    "figures": ("figures",),  # figures in chat and in the viewer
    "citation_jump": ("text_layer",),
    "outline": ("outline",),
    "ai_highlights": ("highlights",),
}

for _feature, _needs in FEATURES.items():
    assert set(_needs) <= set(graph.NEEDS), f"feature {_feature}: unknown stage"


class StageLike(Protocol):
    """The fields read from an `IngestStage` row (or any stand-in)."""

    @property
    def status(self) -> StageStatus | str: ...
    @property
    def progress_done(self) -> Optional[int]: ...
    @property
    def progress_total(self) -> Optional[int]: ...
    @property
    def error_message(self) -> Optional[str]: ...


# `IngestStage` listed on its own: pyright doesn't match its `Mapped[...]`
# columns against the protocol's properties.
StageInput = Union[StageLike, IngestStage, StageStatus, str]


@dataclass(frozen=True)
class FeatureState:
    enabled: bool
    # Needed stages not yet succeeded/skipped (direct needs only).
    waiting_on: list[str] = field(default_factory=list)
    # The stage to point the user at: a failed stage upstream if any, else
    # the first unfinished one. None when enabled or unavailable.
    cause: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class _View:
    status: StageStatus
    progress_done: Optional[int] = None
    progress_total: Optional[int] = None
    error_message: Optional[str] = None


def _view(value: StageInput) -> _View:
    if isinstance(value, str):  # StageStatus is a str too
        return _View(StageStatus(value))
    return _View(
        StageStatus(value.status),
        value.progress_done,
        value.progress_total,
        value.error_message,
    )


def _reason(name: str, view: _View) -> str:
    label = graph.LABELS[name]
    if view.status is StageStatus.FAILED:
        detail = f": {view.error_message}" if view.error_message else ""
        return f"{label} failed{detail}"
    if view.status is StageStatus.QUEUED and view.error_message:
        return f"{label} will retry (last error: {view.error_message})"
    if view.status is StageStatus.RUNNING and view.progress_total:
        return f"Waiting for {label} ({view.progress_done or 0}/{view.progress_total})"
    return f"Waiting for {label}"


def _feature_state(needs: tuple[str, ...], views: Mapping[str, _View]) -> FeatureState:
    if any(name not in views for name in needs):
        return FeatureState(enabled=False, reason="Not available for this document")
    waiting = [n for n in needs if not graph.is_done(views[n].status)]
    if not waiting:
        return FeatureState(enabled=True)
    # Everything the waiting stages depend on, plus themselves, in order.
    chain = graph.topo_sorted(
        {n for w in waiting for n in (*graph.upstream_of(w), w)} & set(views)
    )
    unfinished = [n for n in chain if not graph.is_done(views[n].status)]
    failed = [n for n in unfinished if views[n].status is StageStatus.FAILED]
    cause = failed[0] if failed else unfinished[0]
    return FeatureState(
        enabled=False,
        waiting_on=waiting,
        cause=cause,
        reason=_reason(cause, views[cause]),
    )


def features(stages: Mapping[str, StageInput]) -> dict[str, FeatureState]:
    """Feature map for one paper. `stages`: stage name -> row or status."""
    views = {name: _view(value) for name, value in stages.items()}
    return {
        feature: _feature_state(needs, views) for feature, needs in FEATURES.items()
    }
