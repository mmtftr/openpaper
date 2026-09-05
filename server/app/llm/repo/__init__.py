"""Repo inspection: ingest a paper's companion GitHub repo into a pruned
on-disk snapshot, mount it read-only in a Monty sandbox, and let the chat
agent explore it with `run_python`.

Module layout:

- `storage.py` — where snapshots live on disk, manifest IO, atomic publish.
- `ingest.py`  — GitHub URL validation, SHA resolution, tarball → pruned tree.
- `prelude.py` — host-side `tree`/`read`/`grep` injected into the sandbox.
- `sandbox.py` — the Monty pool, per-run session, poison-safe `run_python`.
- `prompt.py`  — the system-prompt section describing the sandbox subset.
- `code_citations.py` — host-side verification of `file=`/`lines=` citations.
"""
