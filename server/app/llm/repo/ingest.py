"""GitHub repo → pruned on-disk snapshot.

Design constraints that shape this module:

* `MountDir` exposes a whole host directory to the sandbox, so filtering has
  to be *physical*. The pruned tree IS the security boundary.
* No unpruned tree ever touches disk: the tarball is streamed straight from
  the HTTP response through `tarfile` in stream mode (`r|gz`) and pruned
  member-by-member. `extractall` is never called.
* The commit SHA is resolved from the GitHub API and the tarball is fetched
  BY THAT SHA. Permalinks must match the stored bytes, so a resolution
  failure is an ingestion error — never a guess.
"""

from __future__ import annotations

import logging
import re
import tarfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from app.llm.repo import storage

logger = logging.getLogger(__name__)

# A 200 KB cap silently dropped pydantic-ai's 212 KB `agent/__init__.py` in
# the spike, which invites the model to confabulate from training recall of
# a file it cannot see. Real source files get big.
MAX_FILE_BYTES = 1024 * 1024
MAX_KEPT_FILES = 5_000
MAX_KEPT_BYTES = 100 * 1024 * 1024
MAX_SCANNED_MEMBERS = 50_000
MAX_COMPRESSED_BYTES = 200 * 1024 * 1024
# The compressed cap does NOT bound decompression work: `tarfile` must
# inflate a skipped oversize member in full just to reach the next header, so
# a highly compressible archive can expand a <200 MB download into gigabytes.
# This bounds the UNCOMPRESSED bytes the archive declares.
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
# Whole-ingestion wall clock: per-read timeouts alone never trip on a slow
# drip feed.
MAX_INGEST_SECONDS = 300.0

GITHUB_API = "https://api.github.com"
CODELOAD_HOST = "codeload.github.com"
# The only redirect target GitHub uses for archive downloads.
ALLOWED_REDIRECT_HOSTS = {CODELOAD_HOST, "objects.githubusercontent.com"}

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0
DOWNLOAD_TIMEOUT = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
API_TIMEOUT = httpx.Timeout(20.0, connect=CONNECT_TIMEOUT)

SKIP_DIRS = {
    ".git", ".github", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build",
    ".idea", ".vscode", "site", "_build", ".tox", "htmlcov", ".next",
    ".turbo", ".gradle", "target", "vendor",
}

BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf",
    ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".whl", ".so", ".dylib",
    ".dll", ".exe", ".bin", ".pyc", ".pyo", ".pt", ".pth", ".ckpt", ".onnx",
    ".safetensors", ".npy", ".npz", ".h5", ".parquet", ".db", ".sqlite",
    ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".mp3", ".wav",
    ".mov", ".avi", ".jsonl", ".lock", ".ipynb_checkpoints", ".pkl",
    ".pickle", ".bmp", ".tiff", ".class", ".jar", ".wasm",
}

# Owner/repo grammar per GitHub's own rules. Deliberately strict: the SSRF
# surface of this feature is exactly one host, and it stays that way only if
# nothing else can reach the URL builder.
_OWNER_RE = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO_RE = r"[A-Za-z0-9._-]{1,100}"
_URL_RE = re.compile(
    rf"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>{_OWNER_RE})/(?P<repo>{_REPO_RE})"
    rf"(?:\.git)?/?$"
)
_SHORTHAND_RE = re.compile(rf"^(?P<owner>{_OWNER_RE})/(?P<repo>{_REPO_RE})$")

# Paths carrying these can't be cited unambiguously (`|` and `]` are the
# evidence-marker delimiters) or can corrupt tool output.
_UNSAFE_PATH_CHARS_RE = re.compile(r"[\x00-\x1f\x7f|\]]")


class IngestError(RuntimeError):
    """Any failure that should land in `paper_repos.error`."""


@dataclass
class RepoRef:
    owner: str
    repo: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass
class IngestResult:
    owner: str
    repo: str
    ref: str
    commit_sha: str
    file_count: int
    total_bytes: int
    storage_prefix: str
    manifest: Dict[str, Any] = field(default_factory=dict)


def parse_repo_url(url: str) -> RepoRef:
    """`https://github.com/owner/repo(.git)` or `owner/repo` → RepoRef.

    Anything else raises: no other host is reachable from this feature.
    """
    text = str(url or "").strip()
    if not text:
        raise IngestError("Repository URL is required.")
    if len(text) > 300:
        raise IngestError("Repository URL is too long.")

    match = _URL_RE.match(text) or _SHORTHAND_RE.match(text)
    if not match:
        raise IngestError(
            "Only public GitHub repository URLs are supported "
            "(e.g. https://github.com/owner/repo)."
        )
    owner = match.group("owner")
    repo = match.group("repo")
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    # Belt and braces: the character classes already exclude these, but path
    # components are about to be interpolated into a URL and a filesystem
    # path, so assert it rather than trust the regex.
    for component in (owner, repo):
        if not component or component in {".", ".."} or "/" in component or ".." in component:
            raise IngestError(f"Invalid repository path component: {component!r}")
    return RepoRef(owner=owner, repo=repo)


def resolve_head(ref: RepoRef) -> Tuple[str, str]:
    """(default_branch, head_sha) via the public GitHub API.

    Never infers a SHA from the archive: a permalink that doesn't match the
    stored bytes is worse than no permalink.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "openpaper-repo-inspection",
    }
    try:
        with httpx.Client(timeout=API_TIMEOUT, follow_redirects=False) as client:
            meta = client.get(
                f"{GITHUB_API}/repos/{ref.owner}/{ref.repo}", headers=headers
            )
            if meta.status_code == 404:
                raise IngestError(f"Repository {ref.slug} not found (or private).")
            if meta.status_code == 403:
                raise IngestError(
                    "GitHub rate limit reached while resolving the repository. "
                    "Try again in a few minutes."
                )
            if meta.status_code in (301, 302, 307, 308):
                # A renamed repo answers with a redirect and an empty body;
                # `raise_for_status` ignores 3xx and `.json()` would then blow
                # up with a bare JSONDecodeError.
                raise IngestError(
                    f"Repository {ref.slug} has moved. Use its current URL."
                )
            meta.raise_for_status()
            try:
                payload = meta.json()
            except ValueError as exc:
                raise IngestError(
                    f"GitHub returned an unreadable response for {ref.slug}."
                ) from exc
            branch = str(payload.get("default_branch") or "").strip()
            if not branch:
                raise IngestError(f"Could not determine the default branch of {ref.slug}.")

            head = client.get(
                f"{GITHUB_API}/repos/{ref.owner}/{ref.repo}/commits/{branch}",
                headers={**headers, "Accept": "application/vnd.github.sha"},
            )
            if head.status_code >= 400:
                raise IngestError(
                    f"Could not resolve the head commit of {ref.slug}@{branch}."
                )
            sha = head.text.strip().lower()
    except IngestError:
        raise
    except httpx.HTTPError as exc:
        raise IngestError(f"GitHub request failed: {exc}") from exc

    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise IngestError(f"GitHub returned an unusable commit sha for {ref.slug}.")
    return branch, sha


class _CappedReader:
    """File-like adapter over an httpx byte iterator with a hard total cap.

    Lets `tarfile` stream straight off the socket — the compressed archive
    never lands on disk, and a zip-bomb-sized download is cut off at the cap
    instead of filling the volume.
    """

    def __init__(self, chunks: Iterable[bytes], cap: int):
        self._chunks = iter(chunks)
        self._cap = cap
        self._buffer = b""
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            while self._fill():
                pass
            out, self._buffer = self._buffer, b""
            return out
        while len(self._buffer) < size:
            if not self._fill():
                break
        out, self._buffer = self._buffer[:size], self._buffer[size:]
        return out

    def _fill(self) -> bool:
        try:
            chunk = next(self._chunks)
        except StopIteration:
            return False
        self.total += len(chunk)
        if self.total > self._cap:
            raise IngestError(
                f"Repository archive exceeds the {self._cap // (1024 * 1024)} MB "
                "download cap."
            )
        self._buffer += chunk
        return True


def _safe_relative_path(name: str) -> Optional[str]:
    """Strip the archive's top-level `{repo}-{sha}/` dir and validate.

    Returns the repo-relative POSIX path, or None when the member must be
    skipped (tar slip, absolute path, unsafe characters, skipped directory).
    """
    raw = str(name or "").replace("\\", "/")
    if not raw or raw.startswith("/"):
        return None
    parts = PurePosixPath(raw).parts
    if len(parts) < 2:
        return None
    rel_parts = parts[1:]
    for part in rel_parts:
        if part in {"", ".", ".."} or part.startswith("/"):
            return None
        if part in SKIP_DIRS:
            return None
    rel = "/".join(rel_parts)
    if _UNSAFE_PATH_CHARS_RE.search(rel):
        return None
    if len(rel) > 400:
        return None
    return rel


def _looks_texty(data: bytes, suffix: str) -> bool:
    """Validate the WHOLE file, not just a prefix.

    Files are capped at 1 MB, so a full check is cheap — and sniffing only
    the first 4 KB would let binary content past the header land in a mount
    the sandbox is told is text-only.
    """
    if suffix in BINARY_EXTS:
        return False
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _extract_pruned(
    stream: "_CappedReader", target: Path, deadline: Optional[float] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Prune while extracting. Returns (files, skip counters)."""
    files: List[Dict[str, Any]] = []
    skipped = {
        "binary": 0, "oversize": 0, "unsafe_path": 0, "non_regular": 0,
        "scanned": 0,
    }
    kept_bytes = 0
    declared_bytes = 0

    with tarfile.open(fileobj=stream, mode="r|gz") as tar:  # type: ignore[arg-type]
        for member in tar:
            skipped["scanned"] += 1
            if skipped["scanned"] > MAX_SCANNED_MEMBERS:
                raise IngestError(
                    f"Repository has more than {MAX_SCANNED_MEMBERS:,} archive "
                    "entries — too large to inspect."
                )
            if deadline is not None and time.monotonic() > deadline:
                raise IngestError(
                    "Repository ingestion took too long and was stopped."
                )
            # Checked from the member HEADER, before the body is inflated, so
            # a decompression bomb is rejected without doing its work.
            declared_bytes += max(0, int(getattr(member, "size", 0) or 0))
            if declared_bytes > MAX_UNCOMPRESSED_BYTES:
                raise IngestError(
                    "Repository archive expands to more than "
                    f"{MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB — too large "
                    "to inspect."
                )
            # Regular files only: symlinks/hardlinks/devices are how tar
            # archives escape their extraction root.
            if not member.isreg() or member.issym() or member.islnk():
                if not member.isdir():
                    skipped["non_regular"] += 1
                continue
            rel = _safe_relative_path(member.name)
            if rel is None:
                skipped["unsafe_path"] += 1
                continue
            if member.size > MAX_FILE_BYTES:
                skipped["oversize"] += 1
                continue

            handle = tar.extractfile(member)
            if handle is None:
                skipped["non_regular"] += 1
                continue
            data = handle.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                skipped["oversize"] += 1
                continue
            suffix = PurePosixPath(rel).suffix.lower()
            if not _looks_texty(data, suffix):
                skipped["binary"] += 1
                continue

            if len(files) >= MAX_KEPT_FILES:
                raise IngestError(
                    f"Repository has more than {MAX_KEPT_FILES:,} text files — "
                    "too large to inspect."
                )
            if kept_bytes + len(data) > MAX_KEPT_BYTES:
                raise IngestError(
                    f"Repository exceeds the {MAX_KEPT_BYTES // (1024 * 1024)} MB "
                    "text-content cap — too large to inspect."
                )

            # `resolve_within` is the authoritative containment check; the
            # path sanitizer above is the cheap first pass.
            out_path = storage.resolve_within(target, rel)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
            kept_bytes += len(data)
            files.append({"path": rel, "size": len(data)})

    files.sort(key=lambda entry: entry["path"])
    return files, skipped


_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def _redirect_allowed(location: str) -> bool:
    """Is this a plain HTTPS hop to GitHub's archive CDN?

    Host alone is not enough: a cleartext downgrade, embedded credentials or
    an odd port would all pass a hostname check.
    """
    try:
        parsed = urlparse(str(location or ""))
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    if parsed.hostname not in ALLOWED_REDIRECT_HOSTS:
        return False
    if parsed.username or parsed.password:
        return False
    try:
        if parsed.port not in (None, 443):
            return False
    except ValueError:
        return False
    return True


@contextmanager
def open_archive_stream(client: httpx.Client, url: str):
    """Open the archive stream, allowing exactly ONE hop to GitHub's CDN.

    Redirects are off by default so the fetch cannot be walked to an
    arbitrary host: the single permitted hop is validated against
    `ALLOWED_REDIRECT_HOSTS` before it is followed.
    """
    manager = client.stream("GET", url, follow_redirects=False)
    response = manager.__enter__()
    try:
        if response.status_code in _REDIRECT_STATUSES:
            location = response.headers.get("location", "")
            if not _redirect_allowed(location):
                host = urlparse(location).hostname or "?"
                raise IngestError(
                    f"Refusing to follow archive redirect to {host}."
                )
            # Release the first response before opening the hop.
            manager.__exit__(None, None, None)
            manager = client.stream("GET", location, follow_redirects=False)
            response = manager.__enter__()
            if response.status_code in _REDIRECT_STATUSES:
                raise IngestError("Too many redirects fetching the repository archive.")
        if response.status_code >= 400:
            raise IngestError(
                "Downloading the repository archive failed "
                f"(HTTP {response.status_code})."
            )
        yield response
    finally:
        manager.__exit__(None, None, None)


def ingest_repo(*, paper_id: str, url: str) -> IngestResult:
    """Full ingestion: validate → resolve SHA → stream+prune → publish.

    Raises `IngestError` with a user-facing message on every failure path.
    """
    ref = parse_repo_url(url)
    branch, sha = resolve_head(ref)

    target_dir = storage.snapshot_dir(paper_id, sha)
    tmp_dir = storage.temp_dir(paper_id, sha)
    # Repo content goes under `tree/` so it can never collide with (or be
    # overwritten by) `manifest.json` / `.done`.
    tmp_tree = tmp_dir / storage.TREE_SUBDIR
    tmp_tree.mkdir(parents=True, exist_ok=True)

    archive_url = f"https://{CODELOAD_HOST}/{ref.owner}/{ref.repo}/tar.gz/{sha}"
    started = time.time()
    deadline = time.monotonic() + MAX_INGEST_SECONDS
    try:
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=False) as client:
            with open_archive_stream(client, archive_url) as response:
                reader = _CappedReader(response.iter_bytes(), MAX_COMPRESSED_BYTES)
                files, skipped = _extract_pruned(reader, tmp_tree, deadline)

        if not files:
            raise IngestError(
                f"No readable text files found in {ref.slug} — nothing to inspect."
            )

        total_bytes = sum(int(entry["size"]) for entry in files)
        manifest: Dict[str, Any] = {
            "owner": ref.owner,
            "repo": ref.repo,
            "ref": branch,
            "commit_sha": sha,
            "file_count": len(files),
            "total_bytes": total_bytes,
            "skipped": skipped,
            "ingested_at": time.time(),
            "files": files,
        }
        # Publishing is inside the try so an OSError here surfaces as a
        # normal ingestion error instead of an "unexpected failure".
        storage.write_manifest(tmp_dir, manifest)
        storage.publish(tmp_dir, target_dir)
    except IngestError:
        _cleanup(tmp_dir)
        raise
    except (httpx.HTTPError, tarfile.TarError, OSError) as exc:
        _cleanup(tmp_dir)
        raise IngestError(f"Failed to ingest {ref.slug}: {exc}") from exc

    storage.prune_other_snapshots(paper_id, sha)

    logger.info(
        "Ingested %s@%s for paper %s: %d files, %d bytes in %.1fs",
        ref.slug, sha[:8], paper_id, len(files), total_bytes, time.time() - started,
    )
    return IngestResult(
        owner=ref.owner,
        repo=ref.repo,
        ref=branch,
        commit_sha=sha,
        file_count=len(files),
        total_bytes=total_bytes,
        storage_prefix=f"{paper_id}/{sha}",
        manifest=manifest,
    )


def _cleanup(tmp_dir: Path) -> None:
    import shutil

    shutil.rmtree(tmp_dir, ignore_errors=True)


def github_blob_url(
    owner: str, repo: str, sha: str, path: str, start: Optional[int] = None,
    end: Optional[int] = None,
) -> str:
    """Permalink to the exact bytes we ingested."""
    from urllib.parse import quote

    encoded = "/".join(quote(part, safe="") for part in str(path).split("/") if part)
    url = f"https://github.com/{quote(owner, safe='')}/{quote(repo, safe='')}/blob/{sha}/{encoded}"
    if start:
        url += f"#L{int(start)}"
        if end and int(end) != int(start):
            url += f"-L{int(end)}"
    return url
