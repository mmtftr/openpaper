"""Turn results.json into the tables that go in REPORT.md."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
ROWS = json.loads((HERE / "results.json").read_text())

TASK_LABEL = {
    "T1": "T1 inventory (nanoGPT)",
    "T2": "T2 targeted / LR schedule (nanoGPT)",
    "T3": "T3 cross-file / n_layer flow (nanoGPT)",
    "T4": "T4 search / dropout (nanoGPT)",
    "T5": "T5 navigation / retry logic (pydantic-ai)",
    "T6": "T6 synthesis / streaming events (pydantic-ai)",
}


def key(r):
    return (r["task"], r["model"], r["arm"])


def main() -> None:
    rows = sorted(ROWS, key=key)

    print("## Per-run results\n")
    print("| Task | Arm | Model | Status | Tool calls | Sandbox errors | Resets | "
          "Wall (s) | Sandbox (s) | Requests | In tok | Out tok | E/R/F/U |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        s = r["sandbox"]
        u = r.get("usage") or {}
        c = r.get("citation_counts", {})
        print(
            f"| {r['task']} | {r['arm']} | `{r['model']}` | {r['status']} | "
            f"{s['n_calls']} | {s['n_errors']} | {s['n_resets']} | "
            f"{r['wall_seconds']} | {s['sandbox_seconds']} | "
            f"{u.get('requests', '-')} | {u.get('input_tokens', '-')} | "
            f"{u.get('output_tokens', '-')} | "
            f"{c.get('exact', 0)}/{c.get('repairable', 0)}/"
            f"{c.get('fabricated', 0)}/{c.get('unverified', 0)} |"
        )

    print("\n\n## Arm comparison (same model, same task)\n")
    by_model = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        by_model[r["model"]][r["task"]][r["arm"]] = r
    for model, tasks in by_model.items():
        print(f"\n### `{model}`\n")
        print("| Task | Bare calls | Prelude calls | Bare errors | Prelude errors | "
              "Bare wall | Prelude wall | Bare E/R/F/U | Prelude E/R/F/U |")
        print("|---|---|---|---|---|---|---|---|---|")
        for task in sorted(tasks):
            b = tasks[task].get("bare")
            p = tasks[task].get("prelude")

            def f(r, path, default="-"):
                if r is None:
                    return default
                cur = r
                for k in path:
                    cur = cur.get(k, {}) if isinstance(cur, dict) else default
                return cur if cur != {} else default

            def cites(r):
                if r is None:
                    return "-"
                c = r.get("citation_counts", {})
                return (f"{c.get('exact',0)}/{c.get('repairable',0)}/"
                        f"{c.get('fabricated',0)}/{c.get('unverified',0)}")

            print(f"| {task} | {f(b, ['sandbox','n_calls'])} | "
                  f"{f(p, ['sandbox','n_calls'])} | "
                  f"{f(b, ['sandbox','n_errors'])} | "
                  f"{f(p, ['sandbox','n_errors'])} | "
                  f"{f(b, ['wall_seconds'])} | {f(p, ['wall_seconds'])} | "
                  f"{cites(b)} | {cites(p)} |")

    print("\n\n## Aggregates by (model, arm)\n")
    agg = defaultdict(lambda: defaultdict(int))
    aggf = defaultdict(lambda: defaultdict(float))
    for r in rows:
        k = (r["model"], r["arm"])
        s, c = r["sandbox"], r.get("citation_counts", {})
        u = r.get("usage") or {}
        agg[k]["runs"] += 1
        agg[k]["calls"] += s["n_calls"]
        agg[k]["errors"] += s["n_errors"]
        agg[k]["resets"] += s["n_resets"]
        for f_ in ("exact", "repairable", "fabricated", "unverified"):
            agg[k][f_] += c.get(f_, 0)
        agg[k]["in_tok"] += u.get("input_tokens") or 0
        agg[k]["out_tok"] += u.get("output_tokens") or 0
        agg[k]["requests"] += u.get("requests") or 0
        aggf[k]["wall"] += r["wall_seconds"]

    print("| Model | Arm | Runs | Tool calls | Sandbox errors | Resets | "
          "Total wall (s) | Requests | Input tok | Output tok | "
          "Exact | Repairable | Fabricated | Unverified |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (model, arm), d in sorted(agg.items()):
        print(f"| `{model}` | {arm} | {d['runs']} | {d['calls']} | {d['errors']} | "
              f"{d['resets']} | {aggf[(model, arm)]['wall']:.0f} | "
              f"{d['requests']} | {d['in_tok']} | {d['out_tok']} | "
              f"{d['exact']} | {d['repairable']} | {d['fabricated']} | "
              f"{d['unverified']} |")

    print("\n\n## Sandbox errors encountered (verbatim first lines)\n")
    seen = set()
    for r in rows:
        for c in r["tool_calls"]:
            if c["ok"]:
                continue
            first = [l for l in c["output"].split("\n") if l.strip()]
            msg = next(
                (l for l in reversed(first)
                 if "Error" in l or "error" in l), first[-1] if first else "?"
            )[:140]
            k = (r["model"], msg)
            if k in seen:
                continue
            seen.add(k)
            print(f"- `{r['task']}/{r['arm']}/{r['model']}` call {c['n']}: "
                  f"`{msg.strip()}`")

    print("\n\n## Helper usage (prelude arm)\n")
    for r in rows:
        if r["arm"] != "prelude" or not r.get("helper_calls"):
            continue
        counts = defaultdict(int)
        for h in r["helper_calls"]:
            counts[h.split("(")[0]] += 1
        print(f"- `{r['task']}/{r['model']}`: "
              f"{dict(sorted(counts.items()))} "
              f"({len(r['helper_calls'])} helper calls in "
              f"{r['sandbox']['n_calls']} run_python calls)")

    print("\n\n## Truncation events\n")
    for r in rows:
        t = [c["n"] for c in r["tool_calls"] if c["truncated"]]
        if t:
            print(f"- `{r['task']}/{r['arm']}/{r['model']}`: calls {t} hit the "
                  f"6000-char cap")


if __name__ == "__main__":
    main()
