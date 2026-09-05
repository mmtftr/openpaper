"""Unit tests for repo ingestion: URL validation and the extraction filter.

The pruned tree IS the security boundary (a `MountDir` exposes a whole host
directory to the sandbox), so these tests exercise the member-by-member
filter directly rather than the network path.
"""

from __future__ import annotations

import io
import tarfile
import time
from pathlib import Path

import pytest

from app.llm.repo.ingest import (
    MAX_FILE_BYTES,
    IngestError,
    _CappedReader,
    _extract_pruned,
    _safe_relative_path,
    github_blob_url,
    open_archive_stream,
    parse_repo_url,
)


# -- URL validation -------------------------------------------------------


@pytest.mark.parametrize(
    "url,owner,repo",
    [
        ("https://github.com/karpathy/nanoGPT", "karpathy", "nanoGPT"),
        ("http://github.com/andyrdt/refusal_direction", "andyrdt", "refusal_direction"),
        ("https://www.github.com/pydantic/pydantic-ai/", "pydantic", "pydantic-ai"),
        ("https://github.com/owner/repo.git", "owner", "repo"),
        ("github.com/owner/repo", "owner", "repo"),
        ("owner/repo", "owner", "repo"),
    ],
)
def test_parse_repo_url_accepts_github(url, owner, repo):
    ref = parse_repo_url(url)
    assert (ref.owner, ref.repo) == (owner, repo)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "https://gitlab.com/owner/repo",
        "https://evil.com/github.com/owner/repo",
        "https://github.com.evil.com/owner/repo",
        "https://github.com/owner",
        "https://github.com/owner/repo/tree/main",  # branch picker is cut from v1
        "https://github.com/../../etc/passwd",
        "https://github.com/owner/../../../etc",
        "file:///etc/passwd",
        "https://127.0.0.1/owner/repo",
        "https://github.com/owner/repo?x=1",
        "https://github.com/owner/re po",
        "a" * 400,
    ],
)
def test_parse_repo_url_rejects_everything_else(url):
    with pytest.raises(IngestError):
        parse_repo_url(url)


def test_github_blob_url_percent_encodes_each_segment():
    url = github_blob_url("o", "r", "a" * 40, "src/my dir/file name.py", 3, 9)
    assert url == (
        f"https://github.com/o/r/blob/{'a' * 40}/src/my%20dir/file%20name.py#L3-L9"
    )
    single = github_blob_url("o", "r", "b" * 40, "a.py", 5, 5)
    assert single.endswith("#L5")
    assert "#L" not in github_blob_url("o", "r", "c" * 40, "a.py")


# -- path sanitization ----------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("repo-sha/src/main.py", "src/main.py"),
        ("repo-sha/README.md", "README.md"),
        ("repo-sha/../../../etc/passwd", None),          # tar slip
        ("/etc/passwd", None),                            # absolute
        ("repo-sha/.git/config", None),                   # skipped dir
        ("repo-sha/node_modules/pkg/index.js", None),
        ("repo-sha/a/__pycache__/x.pyc", None),
        ("repo-sha", None),                               # top dir itself
        ("repo-sha/bad|name.py", None),                   # citation delimiter
        ("repo-sha/bad]name.py", None),
        ("repo-sha/bad\x01name.py", None),                # control char
        ("repo-sha/" + "d/" * 200 + "f.py", None),        # absurdly long
    ],
)
def test_safe_relative_path(name, expected):
    assert _safe_relative_path(name) == expected


# -- extraction filter ----------------------------------------------------


def _tarball(entries) -> _CappedReader:
    """Build an in-memory tar.gz and wrap it in the capped reader."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, payload, *rest in entries:
            kind = rest[0] if rest else "file"
            if kind == "symlink":
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                tar.addfile(info)
                continue
            data = payload if isinstance(payload, bytes) else payload.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    return _CappedReader(iter([buffer.getvalue()]), 200 * 1024 * 1024)


def test_extract_keeps_text_and_drops_the_rest(tmp_path: Path):
    reader = _tarball(
        [
            ("r-sha/main.py", "print('hi')\n"),
            ("r-sha/docs/guide.md", "# Guide\n"),
            ("r-sha/logo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40),   # binary ext
            ("r-sha/blob.dat", b"abc\x00def"),                          # NUL sniff
            ("r-sha/big.py", "x" * (MAX_FILE_BYTES + 10)),              # oversize
            ("r-sha/.git/config", "[core]\n"),                          # skip dir
            ("r-sha/../escape.py", "pwned\n"),                          # tar slip
            ("r-sha/link.py", "main.py", "symlink"),                    # symlink
        ]
    )
    files, skipped = _extract_pruned(reader, tmp_path)

    kept = {entry["path"] for entry in files}
    assert kept == {"main.py", "docs/guide.md"}
    assert (tmp_path / "main.py").read_text() == "print('hi')\n"
    assert not (tmp_path.parent / "escape.py").exists()
    assert skipped["binary"] == 2
    assert skipped["oversize"] == 1
    assert skipped["unsafe_path"] >= 2
    assert skipped["non_regular"] >= 1


def test_extract_reports_sizes_and_sorted_paths(tmp_path: Path):
    reader = _tarball(
        [("r-sha/z.py", "z" * 10), ("r-sha/a.py", "a" * 5), ("r-sha/m/b.py", "b")]
    )
    files, _ = _extract_pruned(reader, tmp_path)
    assert [entry["path"] for entry in files] == ["a.py", "m/b.py", "z.py"]
    assert [entry["size"] for entry in files] == [5, 1, 10]


def test_capped_reader_aborts_past_the_download_cap():
    reader = _CappedReader(iter([b"x" * 100, b"y" * 100]), cap=150)
    with pytest.raises(IngestError):
        reader.read(-1)


def test_extract_aborts_over_the_kept_file_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.llm.repo.ingest.MAX_KEPT_FILES", 2)
    reader = _tarball([(f"r-sha/f{i}.py", f"# {i}\n") for i in range(5)])
    with pytest.raises(IngestError, match="text files"):
        _extract_pruned(reader, tmp_path)


def test_extract_aborts_over_the_kept_byte_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.llm.repo.ingest.MAX_KEPT_BYTES", 20)
    reader = _tarball([("r-sha/a.py", "x" * 15), ("r-sha/b.py", "y" * 15)])
    with pytest.raises(IngestError, match="text-content cap"):
        _extract_pruned(reader, tmp_path)


# -- archive download / redirect policy -----------------------------------


def _client(handler) -> "httpx.Client":
    import httpx

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_archive_stream_follows_one_hop_to_the_github_cdn():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "codeload.github.com":
            return httpx.Response(
                302,
                headers={"location": "https://objects.githubusercontent.com/pkg"},
            )
        assert request.url.host == "objects.githubusercontent.com"
        return httpx.Response(200, content=b"payload")

    with _client(handler) as client:
        with open_archive_stream(
            client, "https://codeload.github.com/o/r/tar.gz/" + "a" * 40
        ) as response:
            assert response.read() == b"payload"


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example.com/payload",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "",
    ],
)
def test_archive_stream_refuses_redirects_off_github(location):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": location})

    with _client(handler) as client:
        with pytest.raises(IngestError, match="redirect"):
            with open_archive_stream(client, "https://codeload.github.com/o/r/x"):
                pass


def test_archive_stream_refuses_a_second_redirect():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "https://objects.githubusercontent.com/again"}
        )

    with _client(handler) as client:
        with pytest.raises(IngestError, match="Too many redirects"):
            with open_archive_stream(client, "https://codeload.github.com/o/r/x"):
                pass


def test_archive_stream_reports_http_errors_cleanly():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _client(handler) as client:
        with pytest.raises(IngestError, match="HTTP 404"):
            with open_archive_stream(client, "https://codeload.github.com/o/r/x"):
                pass


def test_archive_stream_requires_https_for_the_hop():
    """Host alone is not enough — a cleartext downgrade must be refused."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://objects.githubusercontent.com/pkg"}
        )

    with _client(handler) as client:
        with pytest.raises(IngestError, match="redirect"):
            with open_archive_stream(client, "https://codeload.github.com/o/r/x"):
                pass


def test_archive_stream_refuses_credentials_and_odd_ports():
    import httpx

    for location in (
        "https://user:pass@objects.githubusercontent.com/pkg",
        "https://objects.githubusercontent.com:8080/pkg",
    ):
        def handler(request: httpx.Request, loc=location) -> httpx.Response:
            return httpx.Response(302, headers={"location": loc})

        with _client(handler) as client:
            with pytest.raises(IngestError, match="redirect"):
                with open_archive_stream(client, "https://codeload.github.com/o/r/x"):
                    pass


def test_extract_rejects_a_decompression_bomb(tmp_path: Path, monkeypatch):
    """The compressed cap does not bound decompression work: a skipped
    oversize member is still inflated in full to reach the next header."""
    monkeypatch.setattr("app.llm.repo.ingest.MAX_UNCOMPRESSED_BYTES", 5_000)
    reader = _tarball(
        [("r-sha/huge.bin", b"\x00" * 20_000), ("r-sha/a.py", "x")]
    )
    with pytest.raises(IngestError, match="expands to more than"):
        _extract_pruned(reader, tmp_path)


def test_extract_honours_a_wall_deadline(tmp_path: Path):
    import time as _time

    reader = _tarball([(f"r-sha/f{i}.py", "x") for i in range(5)])
    with pytest.raises(IngestError, match="took too long"):
        _extract_pruned(reader, tmp_path, deadline=_time.monotonic() - 1)


def test_binary_content_after_the_first_4kb_is_rejected(tmp_path: Path):
    """Sniffing only a prefix would let binary bytes into a mount the
    sandbox is told is text-only."""
    payload = b"# a python file\n" + b"a" * 8000 + b"\x00\xff\xfe binary tail"
    reader = _tarball([("r-sha/sneaky.py", payload)])
    files, skipped = _extract_pruned(reader, tmp_path)
    assert files == []
    assert skipped["binary"] == 1


def test_metadata_filenames_in_the_repo_are_kept_as_repo_content(tmp_path: Path):
    """A repo's own `manifest.json` must survive: repo content is extracted
    under `tree/`, so it cannot be clobbered by our snapshot metadata."""
    reader = _tarball(
        [("r-sha/manifest.json", '{"real": "repo file"}'), ("r-sha/.done", "x")]
    )
    files, _ = _extract_pruned(reader, tmp_path)
    assert {entry["path"] for entry in files} == {"manifest.json", ".done"}
    assert (tmp_path / "manifest.json").read_text() == '{"real": "repo file"}'


def test_extract_aborts_over_the_scanned_member_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.llm.repo.ingest.MAX_SCANNED_MEMBERS", 3)
    reader = _tarball([(f"r-sha/f{i}.py", "x") for i in range(6)])
    with pytest.raises(IngestError, match="archive"):
        _extract_pruned(reader, tmp_path)
