"""Phase 2: agentic trials.

Each task runs as an independent conversation. Full pydantic-ai message
transcripts are dumped to transcripts/, plus a machine-readable results.json.

Usage:
  uv run python run_trials.py                       # DeepSeek, both arms, all tasks
  uv run python run_trials.py --model gpt-5.4-mini --tasks T2 --arms prelude
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import traceback
from pathlib import Path

from pydantic_ai.usage import UsageLimits

from agent import build_agent, load_env
from citations import render, score_answer, to_dicts
from ingest import get_repo
from sandbox import RepoSandbox

HERE = Path(__file__).parent
TRANSCRIPTS = HERE / "transcripts"
RESULTS = HERE / "results.json"

MAX_TOOL_CALLS = 25
RUN_TIMEOUT = 300  # seconds; a run that hangs past this is killed and recorded

TASKS = {
    "T1": ("nanoGPT", "inventory",
           "What is the structure of this repository? Summarize what each main "
           "file does."),
    "T2": ("nanoGPT", "targeted",
           "How does the learning rate schedule work in training? Quote the "
           "exact code and file/line."),
    "T3": ("nanoGPT", "cross-file",
           "Trace how a config value like n_layer flows from the command line "
           "to the model construction."),
    "T4": ("nanoGPT", "search",
           "Find every place dropout is applied in the model, with file and "
           "code."),
    "T5": ("pydantic-ai", "navigation",
           "Where is retry logic for failed tool calls implemented? Show the "
           "relevant code."),
    "T6": ("pydantic-ai", "synthesis",
           "Explain how streaming events flow from a model response to "
           "user-facing events. Name the key classes."),
}


async def run_one(task_id: str, model_id: str, arm: str) -> dict:
    repo_name, kind, prompt = TASKS[task_id]
    repo_root = get_repo(repo_name)
    prelude = arm == "prelude"
    label = f"{task_id}-{arm}-{model_id}"
    print(f"\n{'=' * 78}\n>>> {label}  [{repo_name} / {kind}]\n{prompt}\n{'=' * 78}")

    rec: dict = {
        "task": task_id, "arm": arm, "model": model_id, "repo": repo_name,
        "kind": kind, "prompt": prompt, "attempts": [],
    }
    t0 = time.time()

    # A fresh sandbox PER ATTEMPT. Reusing one across retries conflates the
    # abandoned attempt's tool calls with the successful one's, which silently
    # inflated the first run of this matrix (35 recorded calls vs 20 real ones).
    for attempt in range(3):
        att_t0 = time.time()
        with RepoSandbox(repo_root, prelude=prelude) as sb:
            agent = build_agent(model_id, sb, prelude=prelude)
            outcome: dict = {"n": attempt + 1}
            try:
                result = await asyncio.wait_for(
                    agent.run(
                        prompt,
                        usage_limits=UsageLimits(tool_calls_limit=MAX_TOOL_CALLS),
                    ),
                    timeout=RUN_TIMEOUT,
                )
                rec["status"] = "ok"
                rec["answer"] = result.output
                usage = result.usage()
                rec["usage"] = {
                    "requests": usage.requests,
                    "tool_calls": getattr(usage, "tool_calls", None),
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                    "details": dict(usage.details or {}),
                }
                TRANSCRIPTS.mkdir(exist_ok=True)
                (TRANSCRIPTS / f"{label}.json").write_bytes(
                    result.all_messages_json()
                )
                outcome["result"] = "ok"
            except asyncio.TimeoutError:
                rec["status"] = "timeout"
                rec["error"] = f"run exceeded {RUN_TIMEOUT}s and was killed"
                outcome["result"] = "timeout"
                print(f"  !! TIMEOUT after {RUN_TIMEOUT}s", flush=True)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                outcome["result"] = "error"
                outcome["error"] = f"{type(e).__name__}: {msg[:500]}"
                rec["status"] = "error"
                rec["error"] = outcome["error"]
                rec["traceback"] = traceback.format_exc()[-1500:]
                print(f"  !! {type(e).__name__}: {msg[:300]}", flush=True)

            outcome["seconds"] = round(time.time() - att_t0, 1)
            outcome["sandbox"] = sb.stats.summary()
            rec["attempts"].append(outcome)
            # Always keep the LAST attempt's sandbox detail.
            rec["sandbox"] = sb.stats.summary()
            rec["tool_calls"] = [
                {"n": c.n, "code": c.code, "output": c.output, "ok": c.ok,
                 "error_class": c.error_class, "elapsed": c.elapsed,
                 "truncated": c.truncated, "session_reset": c.session_reset}
                for c in sb.stats.calls
            ]
            rec["helper_calls"] = list(sb.helpers.call_log) if prelude else []

        if rec["status"] == "ok":
            break
        msg = rec.get("error", "")
        transient = any(s in msg for s in ("429", "503", "500", "timeout", "Timeout"))
        if transient and attempt < 2:
            wait = 20 * (attempt + 1)
            print(f"  .. transient; backing off {wait}s "
                  f"(attempt {attempt + 2}/3)", flush=True)
            await asyncio.sleep(wait)
            continue
        break

    rec.setdefault("status", "error")
    rec.setdefault("answer", "")
    rec["wall_seconds"] = round(time.time() - t0, 1)

    if rec.get("answer"):
        cites, counts = score_answer(rec["answer"], repo_root)
        rec["citations"] = to_dicts(cites)
        rec["citation_counts"] = counts
        print(f"\n--- ANSWER ({len(rec['answer'])} chars) ---")
        print(rec["answer"][:2500])
        print(f"\n--- CITATIONS {counts} ---\n{render(cites)}")
    else:
        rec["citations"], rec["citation_counts"] = [], {
            "exact": 0, "repairable": 0, "fabricated": 0,
            "unverified": 0, "total": 0,
        }

    s = rec["sandbox"]
    print(f"\n--- {label}: status={rec['status']} wall={rec['wall_seconds']}s "
          f"tool_calls={s['n_calls']} errors={s['n_errors']} "
          f"resets={s['n_resets']} ---")
    if s["error_classes"]:
        print(f"    sandbox error classes: {s['error_classes']}")
    return rec


def load_results() -> list[dict]:
    if RESULTS.exists():
        return json.loads(RESULTS.read_text())
    return []


def save_results(rows: list[dict]) -> None:
    RESULTS.write_text(json.dumps(rows, indent=2))


async def main() -> None:
    global RUN_TIMEOUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="DeepSeek-V4-Flash-0731")
    ap.add_argument("--tasks", nargs="*", default=list(TASKS))
    ap.add_argument("--arms", nargs="*", default=["bare", "prelude"])
    ap.add_argument("--timeout", type=int, default=RUN_TIMEOUT)
    args = ap.parse_args()
    RUN_TIMEOUT = args.timeout

    load_env()
    rows = load_results()
    for task_id in args.tasks:
        for arm in args.arms:
            rows = [
                r for r in rows
                if not (r["task"] == task_id and r["arm"] == arm
                        and r["model"] == args.model)
            ]
            rec = await run_one(task_id, args.model, arm)
            rows.append(rec)
            save_results(rows)

    print(f"\n\n{'=' * 78}\nSaved {len(rows)} result rows to {RESULTS}")
    print(f"{'task':6s} {'arm':8s} {'model':26s} {'status':8s} "
          f"{'calls':>5s} {'err':>4s} {'wall':>7s}  citations")
    for r in rows:
        c = r.get("citation_counts", {})
        print(f"{r['task']:6s} {r['arm']:8s} {r['model']:26s} {r['status']:8s} "
              f"{r['sandbox']['n_calls']:5d} {r['sandbox']['n_errors']:4d} "
              f"{r['wall_seconds']:6.1f}s  "
              f"E{c.get('exact', 0)}/R{c.get('repairable', 0)}/"
              f"F{c.get('fabricated', 0)}/U{c.get('unverified', 0)}")


if __name__ == "__main__":
    asyncio.run(main())
