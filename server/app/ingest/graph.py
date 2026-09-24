"""The ingest DAG (docs/INGEST_DESIGN.md §2) as plain data.

```
 source ─┬─► preview
 (sync)  ├─► text_layer ──► metadata ─────────────────► metadata_fallback
         │        │                                      ▲
         └─► ocr ─┼──► figures                           │
                  └──► ocr_repair ─┬─► outline           │
                     (+text_layer) ├─► highlights        │
                                   └─────────────────────┘
```

Pure (no DB, no stage code): the worker, the API and `features.py` all read
it. Stage classes repeat their `needs` for readability; `registry.py` checks
they agree with this table.

Rules the helpers encode:
- a stage may run once every need is `succeeded` or `skipped`;
- supplementary materials get only `SUPPLEMENTARY_STAGES` (no rows at all
  for the others), and that subset is closed under `needs`.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from app.ingest.models import DONE_STATUSES, StageStatus

# Declaration order is a valid topological order (checked below).
NEEDS: dict[str, tuple[str, ...]] = {
    "source": (),
    "text_layer": ("source",),
    "preview": ("source",),
    "ocr": ("source",),
    "figures": ("ocr",),
    "ocr_repair": ("ocr", "text_layer"),
    "metadata": ("text_layer",),
    "metadata_fallback": ("metadata", "ocr_repair"),
    "outline": ("ocr_repair",),
    "highlights": ("ocr_repair", "text_layer"),
}

STAGES: tuple[str, ...] = tuple(NEEDS)

SUPPLEMENTARY_STAGES: frozenset[str] = frozenset(
    {"source", "text_layer", "preview", "ocr", "figures", "ocr_repair", "outline"}
)

# Human-readable names for UI messages ("Needs OCR repair").
LABELS: dict[str, str] = {
    "source": "Upload",
    "text_layer": "Text layer",
    "preview": "Thumbnail",
    "ocr": "OCR",
    "figures": "Figures",
    "ocr_repair": "OCR repair",
    "metadata": "Metadata lookup",
    "metadata_fallback": "Metadata fallback",
    "outline": "Outline",
    "highlights": "AI highlights",
}

# States a stage can be (re)queued from once its needs are done.
WAITING_STATUSES = frozenset({StageStatus.PENDING, StageStatus.BLOCKED})


def validate(
    needs: Mapping[str, tuple[str, ...]],
    supplementary: Iterable[str] = (),
) -> None:
    """Raise ValueError unless `needs` is a DAG over known names, declared in
    topological order, and `supplementary` is closed under it."""
    seen: set[str] = set()
    for name, deps in needs.items():
        for need in deps:
            if need not in needs:
                raise ValueError(f"stage {name!r} needs unknown stage {need!r}")
            if need not in seen:
                # Needed before it is declared: a cycle (or just bad order,
                # which we reject too so iteration order stays topological).
                raise ValueError(
                    f"stage {name!r} needs {need!r}, which is declared after it"
                    " (cycle or out of order)"
                )
        seen.add(name)
    subset = set(supplementary)
    unknown = subset - set(needs)
    if unknown:
        raise ValueError(f"unknown supplementary stages: {sorted(unknown)}")
    for name in subset:
        missing = set(needs[name]) - subset
        if missing:
            raise ValueError(f"supplementary stage {name!r} needs {sorted(missing)}")


validate(NEEDS, SUPPLEMENTARY_STAGES)
assert set(LABELS) == set(NEEDS), "LABELS must name every stage"


def stages_for(is_supplementary: bool) -> tuple[str, ...]:
    """The stages a paper gets, in topological order."""
    if not is_supplementary:
        return STAGES
    return tuple(s for s in STAGES if s in SUPPLEMENTARY_STAGES)


def dependents_of(name: str) -> tuple[str, ...]:
    """Stages that list `name` directly in their needs."""
    _check(name)
    return tuple(s for s, needs in NEEDS.items() if name in needs)


def upstream_of(name: str) -> tuple[str, ...]:
    """Every stage `name` transitively needs, in topological order."""
    _check(name)
    found: set[str] = set()
    todo = list(NEEDS[name])
    while todo:
        need = todo.pop()
        if need not in found:
            found.add(need)
            todo.extend(NEEDS[need])
    return tuple(s for s in STAGES if s in found)


def downstream_of(name: str) -> tuple[str, ...]:
    """Every stage that transitively needs `name`, in topological order.

    Reprocessing `name` resets it plus these; a permanent failure of `name`
    marks these `blocked`.
    """
    _check(name)
    found: set[str] = set()
    for stage in STAGES:  # topological: needs are visited before dependents
        if any(need == name or need in found for need in NEEDS[stage]):
            found.add(stage)
    return tuple(s for s in STAGES if s in found)


def ready_after(statuses: Mapping[str, StageStatus | str]) -> list[str]:
    """Stages that can be queued now, in topological order.

    `statuses` maps each of a paper's stage rows to its status (stages the
    paper has no row for — e.g. `highlights` on a supplementary — are
    absent). Returns the rows that are `pending`/`blocked` and whose needs
    are all `succeeded`/`skipped`. A missing need counts as not done.
    """
    ready: list[str] = []
    for name in STAGES:
        status = statuses.get(name)
        if status is None or StageStatus(status) not in WAITING_STATUSES:
            continue
        if all(is_done(statuses.get(need)) for need in NEEDS[name]):
            ready.append(name)
    return ready


def blocked_by(name: str, statuses: Mapping[str, StageStatus | str]) -> list[str]:
    """Rows to mark `blocked` because `name` failed: its waiting downstream."""
    return [
        s
        for s in downstream_of(name)
        if s in statuses and StageStatus(statuses[s]) in WAITING_STATUSES
    ]


def is_done(status: StageStatus | str | None) -> bool:
    """`succeeded` or `skipped`: dependents may run."""
    return status is not None and StageStatus(status) in DONE_STATUSES


def _check(name: str) -> None:
    if name not in NEEDS:
        raise KeyError(f"unknown stage {name!r}")


def topo_sorted(names: Iterable[str]) -> list[str]:
    wanted = set(names)
    return [s for s in STAGES if s in wanted]
