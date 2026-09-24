"""Stage name -> `Stage` class, checked against the graph at import."""

from __future__ import annotations

from app.ingest import graph
from app.ingest.stages.base import Stage
from app.ingest.stages.figures import Figures
from app.ingest.stages.highlights import Highlights
from app.ingest.stages.metadata import Metadata
from app.ingest.stages.metadata_fallback import MetadataFallback
from app.ingest.stages.ocr import Ocr
from app.ingest.stages.ocr_repair import OcrRepair
from app.ingest.stages.outline import Outline
from app.ingest.stages.preview import Preview
from app.ingest.stages.source import Source
from app.ingest.stages.text_layer import TextLayer

STAGE_CLASSES: dict[str, type[Stage]] = {
    cls.name: cls
    for cls in (
        Source,
        TextLayer,
        Preview,
        Ocr,
        Figures,
        OcrRepair,
        Metadata,
        MetadataFallback,
        Outline,
        Highlights,
    )
}


def check_against_graph() -> None:
    """Raise ValueError if the classes and `graph` disagree."""
    if set(STAGE_CLASSES) != set(graph.NEEDS):
        raise ValueError(
            f"registry {sorted(STAGE_CLASSES)} != graph {sorted(graph.NEEDS)}"
        )
    for name, cls in STAGE_CLASSES.items():
        if tuple(cls.needs) != graph.NEEDS[name]:
            raise ValueError(f"{name}: needs {cls.needs} != graph {graph.NEEDS[name]}")
        supplementary = name in graph.SUPPLEMENTARY_STAGES
        if cls.applies_to_supplementary != supplementary:
            raise ValueError(f"{name}: applies_to_supplementary disagrees with graph")


check_against_graph()


def get_stage(name: str) -> Stage:
    """A fresh instance of the named stage (stages hold no state)."""
    try:
        return STAGE_CLASSES[name]()
    except KeyError:
        raise KeyError(f"unknown stage {name!r}") from None
