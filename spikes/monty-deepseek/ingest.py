"""Phase 1: GitHub repo -> filtered on-disk tree suitable for a Monty read-only mount.

Monty's MountDir exposes a whole host directory, so filtering has to be
physical: we extract the tarball, then copy only the text/code files we want
into a pruned tree that becomes the mount root.
"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

import httpx

HERE = Path(__file__).parent
REPOS = HERE / "repos"

# A 200 KB cap looked reasonable but silently dropped the two most important
# files in pydantic-ai -- agent/__init__.py (212 KB) and models/openai.py
# (282 KB) -- which made two of the six trial questions unanswerable. Real
# source files get big; 1 MB is the safer cap for text/code.
MAX_FILE_BYTES = 1024 * 1024

SKIP_DIRS = {
    ".git", ".github", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build",
    ".idea", ".vscode", "site", "_build", ".tox", "htmlcov",
}

BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf",
    ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".whl", ".so", ".dylib",
    ".dll", ".exe", ".bin", ".pyc", ".pyo", ".pt", ".pth", ".ckpt", ".onnx",
    ".safetensors", ".npy", ".npz", ".h5", ".parquet", ".db", ".sqlite",
    ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".mp3", ".wav",
    ".mov", ".avi", ".jsonl", ".lock",
}


def download(owner: str, repo: str, branch: str) -> Path:
    REPOS.mkdir(exist_ok=True)
    dest = REPOS / f"{repo}.tar.gz"
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  cached tarball: {dest} ({dest.stat().st_size:,} bytes)")
        return dest
    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{branch}"
    print(f"  downloading {url}")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as r:
        r.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)
    print(f"  -> {dest} ({dest.stat().st_size:,} bytes)")
    return dest


def extract(tarball: Path, repo: str) -> Path:
    raw = REPOS / f"{repo}_raw"
    if raw.exists():
        print(f"  cached extraction: {raw}")
        return next(raw.iterdir())
    raw.mkdir(parents=True)
    print(f"  extracting {tarball.name}")
    with tarfile.open(tarball) as tf:
        tf.extractall(raw, filter="data")
    return next(raw.iterdir())


def is_texty(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTS:
        return False
    try:
        head = path.open("rb").read(4096)
    except OSError:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        # allow a truncated multibyte char at the boundary
        try:
            head[:-4].decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def build_clean_tree(src_root: Path, repo: str) -> tuple[Path, dict]:
    clean = REPOS / f"{repo}_clean"
    if clean.exists():
        shutil.rmtree(clean)
    clean.mkdir(parents=True)

    stats = {"kept": 0, "skipped_dir": 0, "skipped_binary": 0,
             "skipped_large": 0, "bytes": 0}

    def walk(d: Path) -> None:
        for entry in sorted(d.iterdir()):
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in SKIP_DIRS:
                    stats["skipped_dir"] += 1
                    continue
                walk(entry)
            elif entry.is_file():
                if entry.stat().st_size > MAX_FILE_BYTES:
                    stats["skipped_large"] += 1
                    continue
                if not is_texty(entry):
                    stats["skipped_binary"] += 1
                    continue
                rel = entry.relative_to(src_root)
                out = clean / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(entry, out)
                stats["kept"] += 1
                stats["bytes"] += entry.stat().st_size

    walk(src_root)
    return clean, stats


def ingest(owner: str, repo: str, branch: str) -> tuple[Path, dict]:
    print(f"\n=== {owner}/{repo}@{branch} ===")
    tarball = download(owner, repo, branch)
    src = extract(tarball, repo)
    clean, stats = build_clean_tree(src, repo)
    print(f"  clean tree: {clean}")
    print(f"  kept {stats['kept']} files ({stats['bytes']:,} bytes); "
          f"skipped {stats['skipped_binary']} binary, "
          f"{stats['skipped_large']} oversized, {stats['skipped_dir']} dirs")
    return clean, stats


TARGETS = {
    "nanoGPT": ("karpathy", "nanoGPT", "master"),
    "pydantic-ai": ("pydantic", "pydantic-ai", "main"),
}


def get_repo(name: str) -> Path:
    """Return the mount-ready clean tree, ingesting if needed."""
    owner, repo, branch = TARGETS[name]
    clean = REPOS / f"{repo}_clean"
    if clean.exists() and any(clean.iterdir()):
        return clean
    return ingest(owner, repo, branch)[0]


if __name__ == "__main__":
    for name, (owner, repo, branch) in TARGETS.items():
        clean, stats = ingest(owner, repo, branch)
        top = sorted(p.name + ("/" if p.is_dir() else "") for p in clean.iterdir())
        print(f"  top level: {top}")
