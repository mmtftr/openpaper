"""Phase 0b: corrected Monty probe.

probe.py revealed two things that invalidated most of its own results:
  1. `max_duration_secs` is a CUMULATIVE per-session budget, not per-feed.
     One runaway loop permanently poisons the session -- every later feed
     raises TimeoutError instantly.
  2. Modules have no `__name__`, so `import x; x.__name__` was a bad
     availability test.

This version isolates destructive tests in their own sessions and tests
module availability by calling a real function.
"""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

from pydantic_monty import (
    CollectStreams,
    Monty,
    MontyCrashedError,
    MontyError,
    MontyRuntimeError,
    MontySyntaxError,
    MontyTypingError,
    MountDir,
    __version__,
)

HERE = Path(__file__).parent
FIXTURE = HERE / "probe_fixture"
RESULTS: list[dict] = []


def run(session, mount, label: str, code: str, quiet: bool = False) -> dict:
    code = textwrap.dedent(code).strip()
    streams = CollectStreams()
    t0 = time.time()
    entry: dict = {"label": label, "code": code}
    try:
        value = session.feed_run(code, mount=mount, print_callback=streams)
        entry["ok"] = True
        entry["value_repr"] = repr(value)
        entry["error"] = None
    except (MontyRuntimeError, MontySyntaxError) as e:
        entry["ok"] = False
        entry["error_class"] = type(e).__name__
        entry["error"] = e.display("traceback")
        entry["error_short"] = e.display("type-msg")
    except MontyTypingError as e:
        entry["ok"] = False
        entry["error_class"] = "MontyTypingError"
        entry["error"] = entry["error_short"] = e.display()
    except MontyCrashedError as e:
        entry["ok"] = False
        entry["error_class"] = "MontyCrashedError"
        entry["error"] = entry["error_short"] = (
            f"{e} (timed_out={e.timed_out}, exit_status={e.exit_status})"
        )
    except MontyError as e:
        entry["ok"] = False
        entry["error_class"] = type(e).__name__
        entry["error"] = entry["error_short"] = str(e)
    entry["elapsed"] = round(time.time() - t0, 3)
    entry["stdout"] = "".join(t for s, t in streams.output if s == "stdout")
    entry["stderr"] = "".join(t for s, t in streams.output if s == "stderr")
    RESULTS.append(entry)

    if not quiet:
        print(f"\n{'=' * 72}\n## {label}   ({entry['elapsed']}s)")
        print(f"--- code ---\n{code}")
        if entry["stdout"]:
            print(f"--- stdout ---\n{entry['stdout'].rstrip()[:2000]}")
        if entry["ok"]:
            v = entry["value_repr"]
            print(f"--- value ---\n{v[:1500]}{' ...[cut]' if len(v) > 1500 else ''}")
        else:
            print(f"--- {entry['error_class']} ---\n{entry['error']}")
    return entry


GENEROUS = {"max_duration_secs": 600.0, "max_memory": 512 * 1024 * 1024}


def main() -> None:
    print(f"pydantic_monty version: {__version__}")

    with Monty(request_timeout=60.0) as pool:
        mount = MountDir(host_path=FIXTURE, virtual_path="/repo", mode="read-only")

        # ---------------- Session A: the well-behaved exploration session ---
        with pool.checkout(script_name="sandbox.py", limits=GENEROUS) as s:
            run(s, mount, "A1 what does pathlib.Path expose?", """
                from pathlib import Path
                p = Path('/repo')
                sorted(n for n in dir(p) if not n.startswith('_'))
            """)

            run(s, mount, "A2 what does os expose?", """
                import os
                sorted(n for n in dir(os) if not n.startswith('_'))
            """)

            run(s, mount, "A3 what does os.path expose?", """
                import os.path
                sorted(n for n in dir(os.path) if not n.startswith('_'))
            """)

            run(s, mount, "A4 what does re expose?", """
                import re
                sorted(n for n in dir(re) if not n.startswith('_'))
            """)

            # Module availability, tested by calling something real.
            probes = {
                "json": "import json; json.dumps({'a':1})",
                "re": "import re; re.findall('a', 'aba')",
                "pathlib": "from pathlib import Path; str(Path('/repo'))",
                "os": "import os; sorted(os.listdir('/repo'))",
                "os.path": "import os.path; os.path.join('a','b')",
                "math": "import math; math.sqrt(4)",
                "itertools": "import itertools; list(itertools.chain([1],[2]))",
                "collections": "import collections; collections.Counter('aab')",
                "dataclasses": "from dataclasses import dataclass; 'ok'",
                "datetime": "import datetime; str(datetime.date(2020,1,1))",
                "functools": "import functools; 'ok'",
                "typing": "from typing import Optional; 'ok'",
                "textwrap": "import textwrap; 'ok'",
                "string": "import string; 'ok'",
                "difflib": "import difflib; 'ok'",
                "ast": "import ast; 'ok'",
                "csv": "import csv; 'ok'",
                "base64": "import base64; 'ok'",
                "hashlib": "import hashlib; 'ok'",
                "io": "import io; 'ok'",
                "sys": "import sys; 'ok'",
                "time": "import time; 'ok'",
                "random": "import random; 'ok'",
                "statistics": "import statistics; 'ok'",
                "enum": "import enum; 'ok'",
                "heapq": "import heapq; 'ok'",
                "bisect": "import bisect; 'ok'",
                "copy": "import copy; 'ok'",
                "operator": "import operator; 'ok'",
                "unicodedata": "import unicodedata; 'ok'",
                "fnmatch": "import fnmatch; 'ok'",
                "glob": "import glob; 'ok'",
                "subprocess": "import subprocess; 'ok'",
                "socket": "import socket; 'ok'",
                "shutil": "import shutil; 'ok'",
                "urllib": "import urllib; 'ok'",
                "decimal": "import decimal; 'ok'",
                "uuid": "import uuid; 'ok'",
                "logging": "import logging; 'ok'",
                "pprint": "import pprint; 'ok'",
                "numpy": "import numpy; 'ok'",
            }
            avail, missing = [], {}
            for name, code in probes.items():
                r = run(s, mount, f"MOD {name}", code, quiet=True)
                if r["ok"]:
                    avail.append(name)
                else:
                    missing[name] = r.get("error_short", "")
            print(f"\n{'=' * 72}\n## MODULE AVAILABILITY")
            print(f"AVAILABLE ({len(avail)}): {', '.join(avail)}")
            print(f"\nMISSING ({len(missing)}):")
            for k, v in missing.items():
                print(f"  {k:14s} {v.splitlines()[0][:90]}")

            run(s, mount, "A5 recursive walk WITHOUT os.walk/rglob", """
                import os
                from pathlib import Path

                def walk(root):
                    out = []
                    stack = [root]
                    while stack:
                        d = stack.pop()
                        for name in sorted(os.listdir(d)):
                            full = d + '/' + name
                            if os.path.isdir(full):
                                stack.append(full)
                            else:
                                out.append(full)
                    return sorted(out)

                files = walk('/repo')
                print(files)
                files
            """)

            run(s, mount, "A6 persistent state -- reuse walk() from A5", """
                print(len(files), 'files still in scope')
                walk('/repo/pkg')
            """)

            run(s, mount, "A7 Path.glob (non-recursive)", """
                from pathlib import Path
                sorted(str(p) for p in Path('/repo').glob('*.txt'))
            """)

            run(s, mount, "A8 grep across files with re", """
                import re
                hits = []
                for f in files:
                    if not f.endswith('.py'):
                        continue
                    for i, line in enumerate(open(f).read().split('\\n'), 1):
                        if re.search('dropout', line, re.I):
                            hits.append((f, i, line.strip()))
                hits
            """)

            run(s, mount, "A9 open() outside the mount", "open('/etc/passwd').read()")
            run(s, mount, "A10 write to read-only mount", "open('/repo/x.txt','w').write('x')")
            run(s, mount, "A11 missing file inside mount", "open('/repo/nope.txt').read()")
            run(s, mount, "A12 NameError", "totally_undefined_name + 1")
            run(s, mount, "A13 SyntaxError", "def broken(:")
            run(s, mount, "A14 class inheritance", """
                class Base:
                    pass
                class Child(Base):
                    pass
                'ok'
            """)
            run(s, mount, "A15 plain class (no inheritance)", """
                class Counter:
                    def __init__(self):
                        self.n = 0
                    def inc(self):
                        self.n += 1
                        return self.n
                c = Counter()
                (c.inc(), c.inc())
            """)
            run(s, mount, "A16 yield / generator", """
                def gen(n):
                    for i in range(n):
                        yield i
                list(gen(3))
            """)
            run(s, mount, "A17 match statement", """
                x = 3
                match x:
                    case 3:
                        r = 'three'
                    case _:
                        r = 'other'
                r
            """)
            run(s, mount, "A18 walrus operator", """
                xs = [1,2,3]
                if (n := len(xs)) > 2:
                    out = n
                out
            """)
            run(s, mount, "A19 dataclass + f-string", """
                from dataclasses import dataclass
                @dataclass
                class Hit:
                    path: str
                    line: int
                h = Hit('a.py', 3)
                f'{h.path}:{h.line}'
            """)
            run(s, mount, "A20 lambda / sorted key / comprehension", """
                xs = [('b', 2), ('a', 1)]
                (sorted(xs, key=lambda t: t[0]), {k: v for k, v in xs},
                 [x for x, _ in xs], set('abc'))
            """)
            run(s, mount, "A21 stat / size / isdir", """
                import os
                from pathlib import Path
                p = Path('/repo/notes.txt')
                print(p.stat().st_size, p.is_file(), p.is_dir(), p.exists())
                print(os.path.getsize('/repo/notes.txt'), os.path.isdir('/repo/pkg'))
                'stat ok'
            """)
            run(s, mount, "A22 escape probe: os attributes", """
                import os
                [n for n in dir(os) if 'sys' in n or 'exec' in n or 'spawn' in n or 'popen' in n]
            """)
            run(s, mount, "A23 __builtins__ / __import__ / eval / exec", """
                names = []
                for n in ['__import__', 'eval', 'exec', 'compile', 'globals', 'locals', 'open', 'vars']:
                    try:
                        names.append((n, True))
                    except Exception:
                        names.append((n, False))
                names
            """)
            run(s, mount, "A24 try/except FileNotFoundError", """
                try:
                    open('/repo/missing.txt').read()
                except FileNotFoundError as e:
                    result = f'caught: {e}'
                result
            """)
            run(s, mount, "A25 big print volume (10k lines)", """
                for i in range(10000):
                    print('line', i)
                'printed'
            """, quiet=True)
            last = RESULTS[-1]
            print(f"\n{'=' * 72}\n## A25 big print volume: ok={last['ok']} "
                  f"stdout_bytes={len(last['stdout'])} elapsed={last['elapsed']}s")

            run(s, mount, "A26 external_lookup host function", """
                host_grep('dropout')
            """, quiet=True)
            print(f"## A26 (no external_lookup passed) -> {RESULTS[-1].get('error_short','')[:80]}")

        # external_lookup with the kwarg actually supplied
        with pool.checkout(limits=GENEROUS) as s2:
            streams = CollectStreams()
            try:
                val = s2.feed_run(
                    "hits = host_grep('dropout')\nprint('host says', hits)\nhits",
                    mount=mount,
                    print_callback=streams,
                    external_lookup={
                        "host_grep": lambda needle: [
                            str(p) for p in FIXTURE.rglob("*.py")
                            if needle in p.read_text()
                        ]
                    },
                )
                print(f"\n{'=' * 72}\n## A27 external_lookup WITH kwarg -> {val!r}")
                RESULTS.append({"label": "A27 external_lookup with kwarg", "ok": True,
                                "value_repr": repr(val), "code": "host_grep('dropout')",
                                "stdout": "".join(t for _, t in streams.output),
                                "error": None, "elapsed": 0})
            except MontyError as e:
                print(f"\n## A27 external_lookup FAILED: {e}")
                RESULTS.append({"label": "A27 external_lookup with kwarg", "ok": False,
                                "error": str(e), "error_short": str(e), "code": "", "elapsed": 0})

            # does external_lookup persist to the NEXT feed without the kwarg?
            try:
                v = s2.feed_run("hits", mount=mount)
                print(f"## A28 state persists after external feed -> {v!r}")
                RESULTS.append({"label": "A28 state persists after external feed",
                                "ok": True, "value_repr": repr(v), "code": "hits",
                                "error": None, "stdout": "", "elapsed": 0})
            except MontyError as e:
                print(f"## A28 FAILED: {e}")

        # ---------------- Session B: the timeout budget experiment ----------
        print(f"\n{'=' * 72}\n## SESSION B: is max_duration_secs cumulative?")
        with pool.checkout(limits={"max_duration_secs": 3.0}) as sb:
            for i in range(4):
                t0 = time.time()
                try:
                    v = sb.feed_run(
                        "t = 0\nfor _ in range(4_000_000):\n    t += 1\nt",
                        mount=mount,
                    )
                    print(f"  B feed {i}: ok -> {v}  ({time.time()-t0:.2f}s)")
                except MontyError as e:
                    print(f"  B feed {i}: {type(e).__name__}: "
                          f"{str(e).splitlines()[0][:90]}  ({time.time()-t0:.2f}s)")
            print("  -> if later feeds fail instantly, the budget is CUMULATIVE")

        # ---------------- Session C: runaway loop, then recovery ------------
        print(f"\n{'=' * 72}\n## SESSION C: runaway loop kill + recovery")
        with pool.checkout(limits={"max_duration_secs": 3.0}) as sc:
            t0 = time.time()
            try:
                sc.feed_run("while True:\n    pass", mount=mount)
            except MontyError as e:
                print(f"  C1 runaway -> {type(e).__name__}: "
                      f"{str(e).splitlines()[0][:90]} ({time.time()-t0:.2f}s)")
            try:
                v = sc.feed_run("1 + 1", mount=mount)
                print(f"  C2 same session after runaway -> {v}")
            except MontyError as e:
                print(f"  C2 same session after runaway -> {type(e).__name__}: "
                      f"{str(e).splitlines()[0][:90]}")
        with pool.checkout(limits={"max_duration_secs": 3.0}) as sc2:
            try:
                v = sc2.feed_run("1 + 1", mount=mount)
                print(f"  C3 FRESH session after runaway -> {v}  (recovery works)")
            except MontyError as e:
                print(f"  C3 FRESH session -> {type(e).__name__}: {e}")

        # ---------------- Session D: memory + recursion limits ---------------
        print(f"\n{'=' * 72}\n## SESSION D: memory / recursion limits")
        with pool.checkout(limits={"max_duration_secs": 30.0,
                                   "max_memory": 64 * 1024 * 1024,
                                   "max_recursion_depth": 100}) as sd:
            try:
                sd.feed_run("big = []\nfor i in range(10_000_000):\n"
                            "    big.append('x' * 200)\nlen(big)", mount=mount)
            except MontyError as e:
                print(f"  D1 memory hog -> {type(e).__name__}: "
                      f"{str(e).splitlines()[-1][:120]}")
            try:
                sd.feed_run("def rec(n):\n    return rec(n+1)\nrec(0)", mount=mount)
            except MontyError as e:
                msg = e.display('type-msg') if hasattr(e, 'display') else str(e)
                print(f"  D2 deep recursion -> {type(e).__name__}: {msg.splitlines()[0][:120]}")
            try:
                v = sd.feed_run("'alive'", mount=mount)
                print(f"  D3 session after limit hits -> {v}")
            except MontyError as e:
                print(f"  D3 session after limit hits -> {type(e).__name__}: "
                      f"{str(e).splitlines()[0][:90]}")

        mount.close()

    out = HERE / "probe2_results.json"
    out.write_text(json.dumps(RESULTS, indent=2))
    print(f"\n\nWrote {len(RESULTS)} results to {out}")


if __name__ == "__main__":
    main()
