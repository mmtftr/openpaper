"""Phase 0e: settle the API surface by CALLING things.

probe4 used getattr(), which Monty rejects for methods
(`TypeError: getattr(): attribute is not a simple value`), producing false
"missing" results. This probes by invoking each candidate for real.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic_monty import Monty, MontyError, MountDir

FIXTURE = Path(__file__).parent / "probe_fixture"

CASES = {
    # pathlib
    "Path.read_text": "Path('/repo/notes.txt').read_text()[:10]",
    "Path.iterdir": "sorted(str(p) for p in Path('/repo').iterdir())",
    "Path.exists": "Path('/repo').exists()",
    "Path.is_dir": "Path('/repo').is_dir()",
    "Path.is_file": "Path('/repo/notes.txt').is_file()",
    "Path.stat().st_size": "Path('/repo/notes.txt').stat().st_size",
    "Path.glob": "list(Path('/repo').glob('*.txt'))",
    "Path.rglob": "list(Path('/repo').rglob('*.py'))",
    "Path.open": "Path('/repo/notes.txt').open().read()[:5]",
    "Path.name/.suffix/.stem": "(Path('/repo/pkg/mod.py').name, Path('/repo/pkg/mod.py').suffix, Path('/repo/pkg/mod.py').stem)",
    "Path.parent": "str(Path('/repo/pkg/mod.py').parent)",
    "Path.joinpath": "str(Path('/repo').joinpath('pkg', 'mod.py'))",
    "Path / operator": "str(Path('/repo') / 'pkg' / 'mod.py')",
    "Path.parts": "Path('/repo/pkg/mod.py').parts",
    "Path.relative_to": "str(Path('/repo/pkg/mod.py').relative_to('/repo'))",
    "Path.as_posix": "Path('/repo/pkg').as_posix()",
    "Path.with_suffix": "str(Path('/repo/a.py').with_suffix('.txt'))",
    "Path.resolve": "str(Path('/repo/pkg/../pkg').resolve())",
    # os
    "os.listdir": "sorted(os.listdir('/repo'))",
    "os.walk": "list(os.walk('/repo'))",
    "os.path.join": "os.path.join('a', 'b')",
    "os.stat": "os.stat('/repo/notes.txt').st_size",
    "os.scandir": "list(os.scandir('/repo'))",
    "os.getcwd": "os.getcwd()",
    "os.sep": "os.sep",
    "os.environ": "str(os.environ)[:40]",
    "os.getenv": "os.getenv('HOME')",
    "os.system": "os.system('echo hi')",
    # str methods
    "str.split": "'a b'.split()",
    "str.splitlines": "'a\\nb'.splitlines()",
    "str.strip/lower/upper": "('  x '.strip(), 'A'.lower(), 'a'.upper())",
    "str.startswith/endswith": "('ab'.startswith('a'), 'ab'.endswith('b'))",
    "str.replace": "'aXa'.replace('X', 'Y')",
    "str.join": "','.join(['a','b'])",
    "str.find/index/count": "('abc'.find('b'), 'abc'.index('b'), 'aab'.count('a'))",
    "str.format": "'{}-{}'.format(1, 2)",
    "str.partition/rsplit": "('a=b'.partition('='), 'a/b/c'.rsplit('/', 1))",
    "str.encode": "'a'.encode()",
    "f-string": "x = 5\nf'{x:03d} {x!r}'",
    # re
    "re.subn": "re.subn('a', 'b', 'aa')",
    "re.VERBOSE": "re.VERBOSE",
    "re.finditer+span": "[(m.group(0), m.span()) for m in re.finditer('a', 'aba')]",
    "re.compile+search": "re.compile('b').search('abc').group(0)",
    "re.MULTILINE ^": "re.findall('^x', 'x\\nx', re.M)",
    "re.groups": "re.search('(a)(b)', 'ab').groups()",
    # json
    "json.load(file)": "json.load(open('/repo/data.json'))",
    "json.loads": "json.loads('{\"a\": 1}')",
    "json.dumps indent": "json.dumps({'a': 1}, indent=2)",
    # itertools / collections / functools-ish
    "itertools.groupby": "[(k, list(g)) for k, g in itertools.groupby('aab')]",
    "itertools.product": "list(itertools.product([1,2],[3]))",
    "itertools.chain": "list(itertools.chain([1],[2]))",
    "itertools.islice": "list(itertools.islice(range(10), 3))",
    "collections.Counter": "collections.Counter('aab').most_common()",
    "collections.defaultdict": "d = collections.defaultdict(list)\nd['a'].append(1)\ndict(d)",
    "collections.OrderedDict": "collections.OrderedDict([('a',1)])",
    "collections.deque": "list(collections.deque([1,2]))",
    "collections.namedtuple": "P = collections.namedtuple('P', 'x y')\nP(1,2).x",
    # builtins / language
    "sorted(key=)": "sorted([('b',1),('a',2)], key=lambda t: t[0])",
    "list.sort(key=)": "xs = [3,1]\nxs.sort()\nxs",
    "dict comprehension": "{k: v for k, v in [('a', 1)]}",
    "set operations": "({1,2} & {2,3}, {1,2} | {3})",
    "enumerate(start=)": "list(enumerate('ab', 1))",
    "zip": "list(zip([1,2],'ab'))",
    "isinstance": "isinstance(1, int)",
    "hasattr": "hasattr('a', 'split')",
    "try/except/finally": "try:\n    1/0\nexcept ZeroDivisionError:\n    r = 'caught'\nfinally:\n    r = r + '!'\nr",
    "raise custom": "try:\n    raise ValueError('boom')\nexcept ValueError as e:\n    str(e)",
    "with open(...)": "with open('/repo/notes.txt') as f:\n    data = f.read()\ndata[:5]",
    "nested functions/closure": "def outer(a):\n    def inner(b):\n        return a + b\n    return inner(1)\nouter(2)",
    "default+kwargs args": "def f(a, b=2, *args, **kw):\n    return (a, b, args, sorted(kw))\nf(1, 3, 4, x=5)",
    "ternary": "'yes' if 1 else 'no'",
    "star unpacking": "a, *rest = [1,2,3]\n(a, rest)",
    "slicing/negative idx": "([1,2,3][::-1], [1,2,3][-1], 'abc'[1:])",
    "while/break/continue": "n=0\nwhile True:\n    n+=1\n    if n>3:\n        break\nn",
    "list.append/extend/pop": "xs=[1]\nxs.append(2)\nxs.extend([3])\nxs.pop()\nxs",
    "dict.get/items/keys/values": "d={'a':1}\n(d.get('b','x'), list(d.items()), list(d.keys()), list(d.values()))",
    "sum with generator": "sum(x for x in range(4))",
    "max with key": "max([('a',1),('b',9)], key=lambda t: t[1])",
    "string multiplication": "'-'*5",
    "chained comparison": "1 < 2 < 3",
    "assert": "assert 1 == 1\n'ok'",
    "lambda in sorted+reverse": "sorted([1,3,2], reverse=True)",
    "type annotations on def": "def f(x: int) -> str:\n    return str(x)\nf(1)",
    "global statement": "g = 1\ndef bump():\n    global g\n    g += 1\nbump()\ng",
}

PRELUDE = "import os\nimport re\nimport json\nimport itertools\nimport collections\nfrom pathlib import Path\n"


def main() -> None:
    ok: dict[str, str] = {}
    bad: dict[str, str] = {}
    with Monty(request_timeout=60.0) as pool:
        with MountDir(host_path=FIXTURE, virtual_path="/repo", mode="read-only") as m:
            with pool.checkout(limits={"max_duration_secs": 300.0}) as s:
                s.feed_run(PRELUDE + "'prelude'", mount=m)
                for label, code in CASES.items():
                    try:
                        v = s.feed_run(code, mount=m)
                        ok[label] = repr(v)[:90]
                    except MontyError as e:
                        msg = (
                            e.display("type-msg") if hasattr(e, "display") else str(e)
                        ).splitlines()[-1][:110]
                        bad[label] = msg

    print(f"### WORKS ({len(ok)})")
    for k, v in ok.items():
        print(f"  {k:32s} -> {v}")
    print(f"\n### FAILS ({len(bad)})")
    for k, v in bad.items():
        print(f"  {k:32s} -> {v}")

    Path(__file__).parent.joinpath("probe5_calls.json").write_text(
        json.dumps({"works": ok, "fails": bad}, indent=2)
    )
    print("\nWrote probe5_calls.json")


if __name__ == "__main__":
    main()
