"""Phase 0c: pin down Monty's limit semantics -- the operationally critical bit.

Questions:
  Q1 Is max_duration_secs a cumulative execution budget or a wall-clock
     deadline from session start?
  Q2 Once a limit is hit, is the session permanently poisoned?
  Q3 Does memory accounting reset between feeds, or accumulate?
  Q4 Can a poisoned session be detected cheaply and replaced?
"""

from __future__ import annotations

import time

from pydantic_monty import Monty, MontyError, MountDir
from pathlib import Path

FIXTURE = Path(__file__).parent / "probe_fixture"

BURN = "t = 0\nfor _ in range(20_000_000):\n    t += 1\nt"  # ~1.2s each


def feed(s, code, label, mount=None):
    t0 = time.time()
    try:
        v = s.feed_run(code, mount=mount)
        print(f"  {label}: ok -> {str(v)[:40]}  ({time.time() - t0:.2f}s)")
        return True
    except MontyError as e:
        msg = str(e).splitlines()[-1][:100]
        print(f"  {label}: {type(e).__name__} {msg}  ({time.time() - t0:.2f}s)")
        return False


def main() -> None:
    with Monty(request_timeout=120.0) as pool:
        print("Q1: max_duration_secs=5.0, repeated ~1.2s feeds")
        print("    (if CUMULATIVE, ~feed 4 dies; if WALL-CLOCK-from-start, same;")
        print("     the discriminator is the pause test below)")
        with pool.checkout(limits={"max_duration_secs": 5.0}) as s:
            for i in range(6):
                if not feed(s, BURN, f"feed {i}"):
                    break

        print("\nQ1b: max_duration_secs=5.0, ONE 1.2s feed, then 8s host-side pause,")
        print("     then another feed. Wall-clock-from-start => 2nd feed dies.")
        with pool.checkout(limits={"max_duration_secs": 5.0}) as s:
            feed(s, BURN, "feed before pause")
            time.sleep(8)
            feed(s, BURN, "feed after 8s host pause")

        print("\nQ2: after a TimeoutError, is the session dead forever?")
        with pool.checkout(limits={"max_duration_secs": 2.0}) as s:
            feed(s, "while True:\n    pass", "runaway")
            for i in range(3):
                feed(s, "1 + 1", f"trivial retry {i}")

        print("\nQ3: does memory accounting accumulate across feeds?")
        with pool.checkout(limits={"max_duration_secs": 120.0,
                                   "max_memory": 128 * 1024 * 1024}) as s:
            for i in range(6):
                # each feed allocates ~20MB into a *local* that goes out of scope
                if not feed(s, "tmp = ['x' * 1000 for _ in range(20_000)]\nlen(tmp)",
                            f"alloc {i}"):
                    break

        print("\nQ3b: same but rebinding the SAME name each time (should be GC-able)")
        with pool.checkout(limits={"max_duration_secs": 120.0,
                                   "max_memory": 128 * 1024 * 1024,
                                   "gc_interval": 10000}) as s:
            for i in range(6):
                if not feed(s, "tmp = ['x' * 1000 for _ in range(20_000)]\nlen(tmp)",
                            f"alloc {i}"):
                    break

        print("\nQ4: cheap poison detection + fresh-session recovery")
        with pool.checkout(limits={"max_duration_secs": 2.0}) as s:
            feed(s, "while True:\n    pass", "poison")
            t0 = time.time()
            alive = feed(s, "1", "health check")
            print(f"    health check cost {time.time() - t0:.4f}s, alive={alive}")
        with pool.checkout(limits={"max_duration_secs": 2.0}) as s2:
            feed(s2, "1", "fresh session health check")

        print("\nQ5: request_timeout (parent-side) vs limits (sandbox-side)")
        with pool.checkout(limits=None) as s:
            t0 = time.time()
            try:
                s.feed_run("while True:\n    pass")
            except MontyError as e:
                to = getattr(e, "timed_out", None)
                print(f"  no sandbox limits, request_timeout=120 -> "
                      f"{type(e).__name__} timed_out={to} ({time.time()-t0:.1f}s)")

        print("\nQ6: does a mount survive across feeds if passed every time?")
        with MountDir(host_path=FIXTURE, virtual_path="/repo", mode="read-only") as m:
            with pool.checkout(limits={"max_duration_secs": 60.0}) as s:
                feed(s, "open('/repo/notes.txt').read()", "feed 1 with mount", mount=m)
                feed(s, "open('/repo/notes.txt').read()", "feed 2 WITHOUT mount")
                feed(s, "open('/repo/notes.txt').read()", "feed 3 with mount again", mount=m)


if __name__ == "__main__":
    main()
