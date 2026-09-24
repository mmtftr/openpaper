"""The ingest DAG, the stage registry and the feature map."""

import uuid
from dataclasses import dataclass

import pytest

from app.core.deadline import Deadline
from app.ingest import graph
from app.ingest.features import FEATURES, features
from app.ingest.models import StageStatus
from app.ingest.registry import STAGE_CLASSES, check_against_graph, get_stage
from app.ingest.stages.base import (
    StageContext,
    StageFailed,
    StageSkipped,
    fail_permanent,
    skip,
)

S = StageStatus

# -- graph -----------------------------------------------------------------


def test_graph_matches_the_design():
    assert graph.NEEDS["ocr_repair"] == ("ocr", "text_layer")
    assert graph.NEEDS["metadata_fallback"] == ("metadata", "ocr_repair")
    assert graph.NEEDS["highlights"] == ("ocr_repair", "text_layer")
    assert graph.stages_for(True) == (
        "source",
        "text_layer",
        "preview",
        "ocr",
        "figures",
        "ocr_repair",
        "outline",
    )
    assert graph.stages_for(False) == graph.STAGES


@pytest.mark.parametrize(
    "needs,supplementary,match",
    [
        ({"a": ("b",), "b": ()}, (), "declared after"),  # out of order
        ({"a": ("b",), "b": ("a",)}, (), "declared after"),  # cycle
        ({"a": ("a",)}, (), "declared after"),  # self-loop
        ({"a": ("zzz",)}, (), "unknown stage"),
        ({"a": (), "b": ("a",)}, ("b",), "needs"),  # subset not closed
        ({"a": ()}, ("zzz",), "unknown supplementary"),
    ],
)
def test_validate_rejects_bad_graphs(needs, supplementary, match):
    with pytest.raises(ValueError, match=match):
        graph.validate(needs, supplementary)


def test_dependents_upstream_downstream():
    assert graph.dependents_of("ocr") == ("figures", "ocr_repair")
    assert graph.dependents_of("highlights") == ()
    assert graph.upstream_of("metadata_fallback") == (
        "source",
        "text_layer",
        "ocr",
        "ocr_repair",
        "metadata",
    )
    assert graph.downstream_of("ocr") == (
        "figures",
        "ocr_repair",
        "metadata_fallback",
        "outline",
        "highlights",
    )
    assert graph.downstream_of("metadata") == ("metadata_fallback",)
    assert graph.downstream_of("highlights") == ()
    with pytest.raises(KeyError):
        graph.downstream_of("nope")


def _fresh(supplementary=False, **overrides):
    statuses = {name: S.PENDING for name in graph.stages_for(supplementary)}
    statuses["source"] = S.SUCCEEDED
    statuses.update(overrides)
    return statuses


def test_ready_after_upload():
    assert graph.ready_after(_fresh()) == ["text_layer", "preview", "ocr"]


def test_ready_after_needs_every_dependency():
    statuses = _fresh(text_layer=S.SUCCEEDED, preview=S.SUCCEEDED, ocr=S.RUNNING)
    assert graph.ready_after(statuses) == ["metadata"]
    statuses["ocr"] = S.SUCCEEDED
    assert graph.ready_after(statuses) == ["figures", "ocr_repair", "metadata"]


def test_skipped_counts_as_done_and_is_never_requeued():
    statuses = _fresh(
        text_layer=S.SUCCEEDED,
        preview=S.SUCCEEDED,
        ocr=S.SUCCEEDED,
        figures=S.SUCCEEDED,
        ocr_repair=S.SUCCEEDED,
        metadata=S.SUCCEEDED,
        metadata_fallback=S.SKIPPED,  # metadata resolved and skipped it early
    )
    assert graph.ready_after(statuses) == ["outline", "highlights"]


def test_blocked_rows_become_ready_again_when_their_needs_are_done():
    statuses = _fresh(text_layer=S.SUCCEEDED, ocr=S.SUCCEEDED, ocr_repair=S.BLOCKED)
    assert "ocr_repair" in graph.ready_after(statuses)


def test_blocked_by_marks_waiting_downstream_only():
    statuses = _fresh(ocr=S.FAILED, text_layer=S.SUCCEEDED, metadata=S.RUNNING)
    assert graph.blocked_by("ocr", statuses) == [
        "figures",
        "ocr_repair",
        "metadata_fallback",
        "outline",
        "highlights",
    ]


def test_supplementary_rows_only():
    statuses = _fresh(supplementary=True, text_layer=S.SUCCEEDED, ocr=S.SUCCEEDED)
    assert "metadata" not in graph.ready_after(statuses)
    assert graph.ready_after(statuses) == ["preview", "figures", "ocr_repair"]


# -- registry --------------------------------------------------------------


def test_registry_covers_the_graph():
    check_against_graph()
    assert set(STAGE_CLASSES) == set(graph.STAGES)
    assert get_stage("ocr").resource.value == "ocr"
    assert get_stage("ocr_repair").model_slot == "ingest.ocr_repair"
    assert get_stage("metadata_fallback").model_slot == "ingest.metadata"
    assert get_stage("highlights").model_slot == "ingest.highlights"
    assert get_stage("outline").model_slot == "ingest.outline"
    assert get_stage("source").runs_in_request
    with pytest.raises(KeyError):
        get_stage("nope")


def test_model_slots_exist_for_every_stage_that_uses_one():
    from app.llm.model_slots import SLOT_DEFAULTS

    for cls in STAGE_CLASSES.values():
        if cls.model_slot is not None:
            assert cls.model_slot in SLOT_DEFAULTS
    assert SLOT_DEFAULTS["ingest.ocr_repair"].requires_vision


def test_skip_and_fail_helpers():
    with pytest.raises(StageSkipped) as skipped:
        skip("resolved by metadata")
    assert skipped.value.reason == "resolved by metadata"
    from app.core.errors import ErrorKind, classify

    with pytest.raises(StageFailed) as failed:
        fail_permanent("PDF is encrypted")
    assert classify(failed.value).kind is ErrorKind.PERMANENT
    assert classify(failed.value).message == "PDF is encrypted"


def test_context_progress_and_cpu():
    import asyncio

    seen = []

    async def report(done, total):
        seen.append((done, total))

    ctx = StageContext(
        paper_id=uuid.uuid4(),
        stage="ocr",
        attempt=1,
        is_supplementary=False,
        deadline=Deadline(10),
        report_progress=report,
    )

    async def go():
        await ctx.progress(16, 40)
        return await ctx.cpu(sum, [1, 2, 3])

    assert asyncio.run(go()) == 6
    assert seen == [(16, 40)]


# -- features --------------------------------------------------------------


@dataclass
class Row:
    status: StageStatus
    progress_done: int | None = None
    progress_total: int | None = None
    error_message: str | None = None


def test_just_uploaded_paper_is_readable_but_nothing_else():
    got = features(_fresh())
    assert got["reading"].enabled and got["notes"].enabled
    assert got["manual_highlights"].enabled
    chat = got["chat"]
    assert not chat.enabled
    assert chat.waiting_on == ["ocr_repair"]
    assert chat.cause == "text_layer"  # first unfinished upstream stage
    assert chat.reason == "Waiting for Text layer"


def test_progress_shows_in_the_reason():
    stages = {name: Row(status) for name, status in _fresh().items()}
    stages["text_layer"] = Row(S.SUCCEEDED)
    stages["ocr"] = Row(S.RUNNING, progress_done=16, progress_total=40)
    chat = features(stages)["chat"]
    assert chat.cause == "ocr"
    assert chat.reason == "Waiting for OCR (16/40)"
    assert features(stages)["citation_jump"].enabled


def test_failed_upstream_is_the_cause():
    stages = {name: Row(status) for name, status in _fresh().items()}
    stages["text_layer"] = Row(S.RUNNING)
    stages["ocr"] = Row(S.FAILED, error_message="MISTRAL_API_KEY is not set")
    stages["ocr_repair"] = Row(S.BLOCKED)
    got = features(stages)
    assert got["chat"].cause == "ocr"
    assert got["chat"].reason == "OCR failed: MISTRAL_API_KEY is not set"
    assert got["figures"].cause == "ocr"


def test_pending_retry_is_reported():
    stages = {name: Row(status) for name, status in _fresh().items()}
    stages["preview"] = Row(S.QUEUED, error_message="S3 timed out")
    assert features(stages)["thumbnail"].reason == (
        "Thumbnail will retry (last error: S3 timed out)"
    )


def test_metadata_ready_as_soon_as_the_fallback_is_skipped():
    statuses = _fresh(
        text_layer=S.SUCCEEDED, metadata=S.SUCCEEDED, metadata_fallback=S.SKIPPED
    )
    assert features(statuses)["metadata"].enabled
    # Not resolved: waits for the fallback (and through it, OCR repair).
    statuses["metadata_fallback"] = S.PENDING
    got = features(statuses)["metadata"]
    assert not got.enabled
    assert got.waiting_on == ["metadata_fallback"]
    assert got.cause == "ocr"


def test_everything_done_enables_everything():
    statuses = {name: S.SUCCEEDED for name in graph.STAGES}
    got = features(statuses)
    assert set(got) == set(FEATURES)
    assert all(state.enabled for state in got.values())


def test_supplementary_features_without_stages_are_unavailable():
    statuses = {name: S.SUCCEEDED for name in graph.stages_for(True)}
    got = features(statuses)
    assert got["chat"].enabled and got["outline"].enabled
    assert not got["ai_highlights"].enabled
    assert got["ai_highlights"].waiting_on == []
    assert got["ai_highlights"].reason == "Not available for this document"
    assert not got["metadata"].enabled
