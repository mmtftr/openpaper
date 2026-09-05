"""Smoke-test the run_python tool on both repos, both arms -- no LLM.

Checks in particular that external_lookup helpers can return LARGE strings
across the sandbox boundary (the coordinator flagged this as a risk).
"""

from __future__ import annotations

import time

from ingest import get_repo
from sandbox import RepoSandbox


def show(sb: RepoSandbox, label: str, code: str) -> None:
    out = sb.run_python(code)
    rec = sb.stats.calls[-1]
    print(f"\n--- {label}  (ok={rec.ok} {rec.elapsed}s len={len(out)} "
          f"trunc={rec.truncated}) ---")
    print(out[:1400] + (" ...[cut for display]" if len(out) > 1400 else ""))


def main() -> None:
    nano = get_repo("nanoGPT")
    pai = get_repo("pydantic-ai")

    print("=" * 78)
    print("BARE ARM -- nanoGPT (hand-rolled navigation, no helpers)")
    print("=" * 78)
    with RepoSandbox(nano, prelude=False) as sb:
        show(sb, "listdir", "import os\nsorted(os.listdir('/repo'))")
        show(sb, "hand-rolled recursive walk", """
import os
from pathlib import Path

def walk(root):
    out = []
    stack = [root]
    while stack:
        d = stack.pop()
        for name in sorted(os.listdir(d)):
            full = d + '/' + name
            if Path(full).is_dir():
                stack.append(full)
            else:
                out.append(full)
    return sorted(out)

files = walk('/repo')
print(len(files), 'files')
for f in files:
    print(f)
""".strip())
        show(sb, "state persists: reuse walk()", "len(walk('/repo/config'))")
        show(sb, "read a file", "print(open('/repo/model.py').read()[:400])")
        show(sb, "regex grep", """
import re
hits = []
for f in files:
    if not f.endswith('.py'):
        continue
    for i, line in enumerate(open(f).read().split('\\n'), 1):
        if re.search(r'dropout', line):
            hits.append((f, i, line.strip()))
print(len(hits), 'hits')
for h in hits[:12]:
    print(h)
""".strip())
        show(sb, "truncation check (dump everything)",
             "for f in files:\n    print(open(f).read())")
        print(f"\nBARE nanoGPT stats: {sb.stats.summary()}")

    print("\n" + "=" * 78)
    print("PRELUDE ARM -- nanoGPT (host helpers via external_lookup)")
    print("=" * 78)
    with RepoSandbox(nano, prelude=True) as sb:
        show(sb, "tree()", "print(tree('/repo'))")
        show(sb, "read()", "print(read('/repo/train.py', 1, 40))")
        show(sb, "grep()", "print(grep('dropout', glob='*.py'))")
        show(sb, "compose helper output with own python", """
out = grep('def ', '/repo/model.py', '*.py', 0, 100)
names = [l.split(': ')[1].strip() for l in out.split('\\n') if ': ' in l]
print(len(names))
for n in names[:10]:
    print(n)
""".strip())
        show(sb, "LARGE string across the boundary",
             "big = read('/repo/train.py', 1, 400)\nprint('chars:', len(big))\nlen(big)")
        show(sb, "VERY large grep across pydantic-ai-sized output",
             "big = grep('def ', '/repo', '*.py', 0, 5000)\nprint('chars:', len(big))\nlen(big)")
        print(f"\nPRELUDE nanoGPT stats: {sb.stats.summary()}")

    print("\n" + "=" * 78)
    print("SCALE TEST -- pydantic-ai (2269 files)")
    print("=" * 78)
    with RepoSandbox(pai, prelude=True) as sb:
        t0 = time.time()
        show(sb, "tree() depth 2", "print(tree('/repo', 2))")
        show(sb, "grep across 2269 files",
             "print(grep('class .*Retry', '/repo', '*.py', 1, 20))")
        show(sb, "huge grep -> string size",
             "big = grep('def ', '/repo', '*.py', 0, 100000)\n"
             "print('chars:', len(big), 'lines:', len(big.split(chr(10))))\n'ok'")
        print(f"  (helper calls took {time.time() - t0:.1f}s wall)")
        print(f"\nPRELUDE pydantic-ai stats: {sb.stats.summary()}")

    print("\n" + "=" * 78)
    print("BARE ARM AT SCALE -- pydantic-ai hand-rolled walk (cost check)")
    print("=" * 78)
    with RepoSandbox(pai, prelude=False) as sb:
        show(sb, "hand-rolled walk over 2269 files", """
import os
from pathlib import Path

def walk(root):
    out = []
    stack = [root]
    while stack:
        d = stack.pop()
        for name in sorted(os.listdir(d)):
            full = d + '/' + name
            if Path(full).is_dir():
                stack.append(full)
            else:
                out.append(full)
    return sorted(out)

files = walk('/repo')
print('total files:', len(files))
py = [f for f in files if f.endswith('.py')]
print('py files:', len(py))
""".strip())
        show(sb, "hand-rolled grep over all py files", """
import re
hits = []
for f in py:
    try:
        text = open(f).read()
    except Exception:
        continue
    for i, line in enumerate(text.split('\\n'), 1):
        if re.search(r'class \\w*Retry', line):
            hits.append((f, i, line.strip()))
print('hits:', len(hits))
for h in hits[:15]:
    print(h)
""".strip())
        print(f"\nBARE pydantic-ai stats: {sb.stats.summary()}")

    print("\n" + "=" * 78)
    print("POISON + RESET RECOVERY")
    print("=" * 78)
    with RepoSandbox(nano, prelude=False) as sb:
        sb.run_python("marker = 'i survive'\nmarker")
        print(f"  before: {sb.run_python('marker')[:60]}")
        out = sb.run_python("while True:\n    pass")
        print(f"  runaway -> {out[-260:]}")
        print(f"  after reset, marker -> {sb.run_python('marker')[:120]}")
        print(f"  after reset, fresh work -> {sb.run_python('1 + 1')[:60]}")
        print(f"\nstats: {sb.stats.summary()}")


if __name__ == "__main__":
    main()
