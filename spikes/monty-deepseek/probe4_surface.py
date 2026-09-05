"""Phase 0d: enumerate the exact attribute surface of the modules that DO exist.

`dir()` is not available inside Monty, so we probe candidate names one at a
time with getattr in a try/except and report what actually resolves. The
result is what the agent's system prompt must say -- guessing here is how the
LLM burns tool calls on AttributeError.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic_monty import Monty, MontyError, MountDir

FIXTURE = Path(__file__).parent / "probe_fixture"

CANDIDATES = {
    "os": [
        "listdir", "walk", "path", "stat", "getcwd", "sep", "environ",
        "scandir", "mkdir", "remove", "rename", "system", "popen",
        "getenv", "makedirs", "rmdir", "readlink", "symlink", "chdir",
        "F_OK", "R_OK", "access", "fspath", "sep", "linesep", "curdir",
    ],
    "pathlib.Path('/repo')": [
        "iterdir", "glob", "rglob", "read_text", "read_bytes", "write_text",
        "is_dir", "is_file", "exists", "stat", "name", "suffix", "stem",
        "parent", "parts", "joinpath", "resolve", "absolute", "as_posix",
        "with_suffix", "relative_to", "match", "open", "samefile", "parents",
        "is_absolute", "is_symlink", "touch", "mkdir",
    ],
    "re": [
        "search", "match", "fullmatch", "findall", "finditer", "sub", "subn",
        "split", "compile", "escape", "IGNORECASE", "I", "MULTILINE", "M",
        "DOTALL", "S", "VERBOSE", "X", "Pattern", "Match",
    ],
    "json": ["loads", "dumps", "load", "dump", "JSONDecodeError"],
    "collections": [
        "Counter", "defaultdict", "OrderedDict", "deque", "namedtuple",
        "ChainMap", "UserDict",
    ],
    "itertools": [
        "chain", "groupby", "islice", "count", "cycle", "repeat", "product",
        "permutations", "combinations", "zip_longest", "accumulate",
        "takewhile", "dropwhile", "starmap", "tee", "filterfalse",
    ],
    "math": ["sqrt", "floor", "ceil", "log", "exp", "pi", "inf", "isnan", "pow"],
    "sys": ["version", "platform", "argv", "path", "maxsize", "stdout", "exit"],
    "datetime": ["date", "datetime", "timedelta", "timezone", "time"],
}

BUILTINS = [
    "len", "range", "print", "open", "sorted", "enumerate", "zip", "sum",
    "min", "max", "any", "all", "abs", "round", "str", "int", "float", "bool",
    "list", "dict", "set", "tuple", "frozenset", "type", "isinstance",
    "hasattr", "getattr", "setattr", "repr", "reversed", "map", "filter",
    "dir", "vars", "eval", "exec", "compile", "globals", "locals", "input",
    "__import__", "id", "hash", "iter", "next", "slice", "divmod", "bytes",
    "bytearray", "ord", "chr", "hex", "oct", "bin", "format", "callable",
    "issubclass", "super", "property", "staticmethod", "classmethod",
    "NotImplementedError", "Exception", "ValueError", "KeyError", "IndexError",
    "TypeError", "AttributeError", "FileNotFoundError", "PermissionError",
    "StopIteration", "RuntimeError", "OSError", "ZeroDivisionError",
]


def main() -> None:
    findings: dict[str, dict] = {}
    with Monty(request_timeout=60.0) as pool:
        with MountDir(host_path=FIXTURE, virtual_path="/repo", mode="read-only") as m:
            with pool.checkout(limits={"max_duration_secs": 120.0}) as s:
                for expr, names in CANDIDATES.items():
                    mod = expr.split(".")[0].split("(")[0]
                    imp = (
                        "from pathlib import Path"
                        if expr.startswith("pathlib")
                        else f"import {mod}"
                    )
                    target = "Path('/repo')" if expr.startswith("pathlib") else mod
                    code = (
                        f"{imp}\n"
                        f"_t = {target}\n"
                        f"present = []\n"
                        f"absent = []\n"
                        f"for _n in {names!r}:\n"
                        f"    try:\n"
                        f"        getattr(_t, _n)\n"
                        f"        present.append(_n)\n"
                        f"    except AttributeError:\n"
                        f"        absent.append(_n)\n"
                        f"(present, absent)"
                    )
                    try:
                        present, absent = s.feed_run(code, mount=m)
                    except MontyError as e:
                        print(f"{expr}: PROBE FAILED {e}")
                        continue
                    findings[expr] = {"present": present, "absent": absent}
                    print(f"\n### {expr}")
                    print(f"  HAS ({len(present)}): {', '.join(present)}")
                    print(f"  MISSING ({len(absent)}): {', '.join(absent)}")

                # builtins: NameError, not AttributeError
                code = (
                    "present = []\nabsent = []\n"
                    f"for _n in {BUILTINS!r}:\n"
                    "    try:\n"
                    "        _v = globals()[_n]\n"
                    "        present.append(_n)\n"
                    "    except KeyError:\n"
                    "        absent.append(_n)\n"
                    "(present, absent)"
                )
                try:
                    present, absent = s.feed_run(code, mount=m)
                    print(f"\n### builtins via globals()\n  HAS: {present}\n  MISSING: {absent}")
                    findings["builtins_globals"] = {"present": present, "absent": absent}
                except MontyError as e:
                    print(f"\n### builtins via globals() FAILED: {str(e).splitlines()[-1]}")
                    # fall back: probe one at a time with a NameError catch
                    present, absent = [], []
                    for n in BUILTINS:
                        try:
                            s.feed_run(f"{n}\n'ok'", mount=m)
                            present.append(n)
                        except MontyError:
                            absent.append(n)
                    print(f"  HAS ({len(present)}): {', '.join(present)}")
                    print(f"  MISSING ({len(absent)}): {', '.join(absent)}")
                    findings["builtins"] = {"present": present, "absent": absent}

                # string methods that matter for code search
                code = (
                    "s = 'Hello World'\n"
                    "present = []\nabsent = []\n"
                    "for _n in ['split','splitlines','strip','lower','upper','startswith',"
                    "'endswith','find','rfind','index','replace','join','count','format',"
                    "'partition','rsplit','lstrip','rstrip','encode','isdigit','title','zfill']:\n"
                    "    try:\n"
                    "        getattr(s, _n)\n"
                    "        present.append(_n)\n"
                    "    except AttributeError:\n"
                    "        absent.append(_n)\n"
                    "(present, absent)"
                )
                present, absent = s.feed_run(code, mount=m)
                print(f"\n### str methods\n  HAS: {', '.join(present)}\n  MISSING: {', '.join(absent)}")
                findings["str"] = {"present": present, "absent": absent}

                # how do you test isdir without os.path?
                print("\n### recursive walk using only what exists")
                walk_code = """
import os
from pathlib import Path

def walk(root):
    files = []
    dirs = []
    stack = [root]
    while stack:
        d = stack.pop()
        for name in sorted(os.listdir(d)):
            full = d + '/' + name
            if Path(full).is_dir():
                dirs.append(full)
                stack.append(full)
            else:
                files.append(full)
    return sorted(files), sorted(dirs)

files, dirs = walk('/repo')
print('files:', files)
print('dirs:', dirs)
(len(files), len(dirs))
"""
                try:
                    print(f"  -> {s.feed_run(walk_code, mount=m)}")
                except MontyError as e:
                    print(f"  FAILED: {e}")

    Path(__file__).parent.joinpath("probe4_surface.json").write_text(
        json.dumps(findings, indent=2)
    )
    print("\nWrote probe4_surface.json")


if __name__ == "__main__":
    main()
