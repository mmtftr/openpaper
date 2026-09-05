"""Re-score citations in results.json after a scorer fix (no API calls)."""
import json, sys
from pathlib import Path
from citations import score_answer, to_dicts, render
from ingest import get_repo

HERE = Path(__file__).parent
path = HERE / (sys.argv[1] if len(sys.argv) > 1 else "results.json")
rows = json.loads(path.read_text())
for r in rows:
    if not r.get("answer"):
        continue
    root = get_repo(r["repo"])
    cites, counts = score_answer(r["answer"], root)
    r["citations"], r["citation_counts"] = to_dicts(cites), counts
path.write_text(json.dumps(rows, indent=2))
for r in rows:
    c = r.get("citation_counts", {})
    print(f"{r['task']:3s} {r['arm']:8s} {r['status']:8s} "
          f"E{c.get('exact',0)}/R{c.get('repairable',0)}/"
          f"F{c.get('fabricated',0)}/U{c.get('unverified',0)}")
