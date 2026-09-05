"""Phase 0: Monty Python-API capability probe. No LLM involved.

Records exactly what the sandbox supports and the *verbatim* error text it
produces, since that text is what an LLM has to iterate on.
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


def setup_fixture() -> None:
    FIXTURE.mkdir(exist_ok=True)
    (FIXTURE / "notes.txt").write_text("hello from the host\nline two\n")
    (FIXTURE / "data.json").write_text(json.dumps({"a": 1, "b": [2, 3]}))
    sub = FIXTURE / "pkg"
    sub.mkdir(exist_ok=True)
    (sub / "mod.py").write_text("def f(x):\n    return x * 2\n\nDROPOUT = 0.1\n")
    (sub / "other.py").write_text("import re\nPATTERN = re.compile('dropout')\n")


def run(session, mount, label: str, code: str) -> dict:
    code = textwrap.dedent(code).strip()
    streams = CollectStreams()
    t0 = time.time()
    entry = {"label": label, "code": code}
    try:
        value = session.feed_run(code, mount=mount, print_callback=streams)
        entry["ok"] = True
        entry["value_repr"] = repr(value)
        entry["error"] = None
    except MontyRuntimeError as e:
        entry["ok"] = False
        entry["error_class"] = "MontyRuntimeError"
        entry["error"] = e.display("traceback")
        entry["error_short"] = e.display("type-msg")
    except MontySyntaxError as e:
        entry["ok"] = False
        entry["error_class"] = "MontySyntaxError"
        entry["error"] = e.display("traceback")
        entry["error_short"] = e.display("type-msg")
    except MontyTypingError as e:
        entry["ok"] = False
        entry["error_class"] = "MontyTypingError"
        entry["error"] = e.display()
        entry["error_short"] = str(e)
    except MontyCrashedError as e:
        entry["ok"] = False
        entry["error_class"] = "MontyCrashedError"
        entry["error"] = f"{e} (timed_out={e.timed_out}, exit_status={e.exit_status})"
        entry["error_short"] = entry["error"]
    except MontyError as e:
        entry["ok"] = False
        entry["error_class"] = type(e).__name__
        entry["error"] = str(e)
        entry["error_short"] = str(e)
    entry["elapsed"] = round(time.time() - t0, 3)
    entry["stdout"] = "".join(t for s, t in streams.output if s == "stdout")
    entry["stderr"] = "".join(t for s, t in streams.output if s == "stderr")
    RESULTS.append(entry)

    print(f"\n{'=' * 72}\n## {label}   ({entry['elapsed']}s)")
    print(f"--- code ---\n{code}")
    if entry["stdout"]:
        print(f"--- stdout ---\n{entry['stdout'].rstrip()}")
    if entry["stderr"]:
        print(f"--- stderr ---\n{entry['stderr'].rstrip()}")
    if entry["ok"]:
        v = entry["value_repr"]
        print(f"--- value ---\n{v[:800]}{' ...[cut]' if len(v) > 800 else ''}")
    else:
        print(f"--- {entry['error_class']} ---\n{entry['error']}")
    return entry


def main() -> None:
    setup_fixture()
    print(f"pydantic_monty version: {__version__}")

    limits = {
        "max_duration_secs": 5.0,
        "max_memory": 256 * 1024 * 1024,
        "max_recursion_depth": 200,
    }

    with Monty(request_timeout=30.0) as pool:
        with MountDir(
            host_path=FIXTURE, virtual_path="/repo", mode="read-only"
        ) as mount:
            with pool.checkout(script_name="sandbox.py", limits=limits) as session:
                run(session, mount, "P1 open() a mounted file", """
                    open('/repo/notes.txt').read()
                """)

                run(session, mount, "P2 pathlib read_text", """
                    from pathlib import Path
                    Path('/repo/pkg/mod.py').read_text()
                """)

                run(session, mount, "P3 pathlib iterdir / glob / rglob", """
                    from pathlib import Path
                    listing = sorted(str(p) for p in Path('/repo').iterdir())
                    globbed = sorted(str(p) for p in Path('/repo').rglob('*.py'))
                    print('iterdir:', listing)
                    print('rglob:', globbed)
                    (listing, globbed)
                """)

                run(session, mount, "P4 os.listdir / os.walk", """
                    import os
                    print('listdir:', sorted(os.listdir('/repo')))
                    walked = [(r, sorted(d), sorted(f)) for r, d, f in os.walk('/repo')]
                    print('walk:', walked)
                    walked
                """)

                run(session, mount, "P5 stdlib: json", """
                    import json
                    json.loads(open('/repo/data.json').read())
                """)

                run(session, mount, "P6 stdlib: re", """
                    import re
                    src = open('/repo/pkg/mod.py').read()
                    [m.group(0) for m in re.finditer(r'def \\w+', src)]
                """)

                for mod in [
                    "math", "itertools", "collections", "dataclasses",
                    "datetime", "functools", "typing", "textwrap", "string",
                    "difflib", "ast", "tokenize", "csv", "base64", "hashlib",
                    "io", "sys", "time", "random", "statistics", "enum",
                    "heapq", "bisect", "copy", "operator", "unicodedata",
                    "fnmatch", "posixpath", "urllib", "sqlite3", "subprocess",
                    "socket", "shutil", "tempfile", "glob", "pickle", "struct",
                    "zipfile", "tarfile", "logging", "abc", "inspect", "types",
                ]:
                    run(session, mount, f"P7 import {mod}", f"import {mod}\n{mod}.__name__")

                run(session, mount, "P8 import numpy (third-party)", """
                    import numpy as np
                    np.zeros(3)
                """)

                run(session, mount, "P9 open() outside the mount", """
                    open('/etc/passwd').read()
                """)

                run(session, mount, "P10 write to a read-only mount", """
                    open('/repo/evil.txt', 'w').write('pwned')
                """)

                run(session, mount, "P11 FileNotFoundError inside the mount", """
                    open('/repo/nope.txt').read()
                """)

                run(session, mount, "P12 persistent state (define)", """
                    REPO_FILES = sorted(str(p) for p in __import__('pathlib').Path('/repo').rglob('*'))
                    def head(path, n=3):
                        return open(path).read().split(chr(10))[:n]
                    len(REPO_FILES)
                """)

                run(session, mount, "P13 persistent state (reuse from P12)", """
                    print(REPO_FILES)
                    head('/repo/notes.txt')
                """)

                run(session, mount, "P14 class definition", """
                    class Counter:
                        def __init__(self):
                            self.n = 0
                        def inc(self):
                            self.n += 1
                            return self.n
                    c = Counter()
                    (c.inc(), c.inc())
                """)

                run(session, mount, "P15 class inheritance", """
                    class Base:
                        pass
                    class Child(Base):
                        pass
                    Child()
                """)

                run(session, mount, "P16 dataclasses + f-strings + comprehensions", """
                    from dataclasses import dataclass
                    @dataclass
                    class Hit:
                        path: str
                        line: int
                    hits = [Hit(p, i) for i, p in enumerate(['a', 'b'])]
                    f'{hits[0].path}:{hits[0].line} n={len(hits)}'
                """)

                run(session, mount, "P17 NameError (undefined name)", """
                    totally_undefined_name + 1
                """)

                run(session, mount, "P18 SyntaxError", """
                    def broken(:
                """)

                run(session, mount, "P19 large output / print volume", """
                    for i in range(5):
                        print('x' * 40, i)
                    'done'
                """)

                run(session, mount, "P20 runaway loop (limit kill)", """
                    i = 0
                    while True:
                        i += 1
                """)

                run(session, mount, "P21 session alive after the runaway?", """
                    1 + 1
                """)

                run(session, mount, "P22 deep recursion", """
                    def rec(n):
                        return rec(n + 1)
                    rec(0)
                """)

                run(session, mount, "P23 memory hog", """
                    big = []
                    for i in range(50_000_000):
                        big.append('x' * 100)
                    len(big)
                """)

                run(session, mount, "P24 session alive after memory hog?", """
                    'still here'
                """)

                run(session, mount, "P25 external_lookup host function", """
                    host_grep('dropout')
                """)

                # external_lookup demo needs its own call with the kwarg
                streams = CollectStreams()
                try:
                    val = session.feed_run(
                        "host_grep('dropout')",
                        mount=mount,
                        print_callback=streams,
                        external_lookup={
                            "host_grep": lambda needle: [
                                str(p) for p in FIXTURE.rglob("*.py")
                                if needle in p.read_text()
                            ]
                        },
                    )
                    RESULTS.append({
                        "label": "P25b external_lookup host function (with kwarg)",
                        "code": "host_grep('dropout')  # external_lookup={'host_grep': ...}",
                        "ok": True, "value_repr": repr(val), "error": None,
                        "stdout": "", "stderr": "", "elapsed": 0,
                    })
                    print(f"\n{'=' * 72}\n## P25b external_lookup (with kwarg)\n--- value ---\n{val!r}")
                except MontyError as e:
                    print(f"\nP25b FAILED: {e}")

                run(session, mount, "P26 exec / eval / getattr escape attempts", """
                    import os
                    print(hasattr(os, 'system'), hasattr(os, 'popen'))
                    print([n for n in dir(os) if 'sys' in n or 'exec' in n or 'spawn' in n])
                    'probed'
                """)

                run(session, mount, "P27 __builtins__ / __import__ availability", """
                    print(type(__builtins__))
                    'ok'
                """)

                run(session, mount, "P28 file stat / size", """
                    import os
                    from pathlib import Path
                    p = Path('/repo/notes.txt')
                    print(p.stat().st_size, p.is_file(), p.is_dir(), p.exists())
                    print(os.path.getsize('/repo/notes.txt'))
                    'stat ok'
                """)

                run(session, mount, "P29 sorted/enumerate/zip/any/all/sum/min/max", """
                    xs = [3, 1, 2]
                    (sorted(xs), list(enumerate(xs)), list(zip(xs, 'abc')),
                     any(xs), all(xs), sum(xs), min(xs), max(xs))
                """)

                run(session, mount, "P30 generators / yield", """
                    def gen(n):
                        for i in range(n):
                            yield i * i
                    list(gen(5))
                """)

                run(session, mount, "P31 try/except inside sandbox", """
                    try:
                        open('/repo/missing.txt').read()
                    except FileNotFoundError as e:
                        result = f'caught: {e}'
                    result
                """)

                run(session, mount, "P32 walrus / match / typing generics", """
                    from typing import Optional
                    xs = [1, 2, 3]
                    if (n := len(xs)) > 2:
                        out = n
                    match out:
                        case 3:
                            label = 'three'
                        case _:
                            label = 'other'
                    (out, label)
                """)

    out = HERE / "probe_results.json"
    out.write_text(json.dumps(RESULTS, indent=2))
    print(f"\n\n{'=' * 72}\nWrote {len(RESULTS)} probe results to {out}")

    fails = [r for r in RESULTS if not r.get("ok")]
    print(f"\nFAILED/ERRORED ({len(fails)}):")
    for r in fails:
        print(f"  - {r['label']}: {r.get('error_short', r.get('error', ''))[:120]}")


if __name__ == "__main__":
    main()
