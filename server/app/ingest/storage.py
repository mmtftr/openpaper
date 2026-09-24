"""S3 keys and object I/O for ingest (design §3: everything under `papers/{id}/`).

    papers/{paper_id}/{original-file-name}.pdf   the PDF (`papers.s3_object_key`)
    papers/{paper_id}/preview.png                first-page thumbnail
    papers/{paper_id}/figures/{figure_id}.png    one per `paper_figures` row

Figure keys use the `paper_figures.id` (a fresh uuid per row), so a key is
never reused for a different image; saved chat history refers to them.
Deleting a paper deletes its prefix (`delete_paper_objects`).

Uses the server's `S3Service` (bucket, endpoint, public URL config). Its
boto3 errors are translated for `core.errors.classify`: connection
failures and 5xx (incl. MinIO's 507 "storage full") are temporary, other
client errors permanent.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Callable, TypeVar

from botocore.exceptions import BotoCoreError, ClientError

from app.core.errors import PermanentError, TemporaryError

if TYPE_CHECKING:
    from app.helpers.s3 import S3Service
    from app.ingest.stages.base import StageContext

T = TypeVar("T")

PDF_CONTENT_TYPE = "application/pdf"
PNG_CONTENT_TYPE = "image/png"
DEFAULT_PDF_NAME = "paper.pdf"


# -- keys -----------------------------------------------------------------------


def paper_prefix(paper_id: uuid.UUID | str) -> str:
    return f"papers/{paper_id}/"


def safe_file_name(filename: str | None) -> str:
    """A key-safe `*.pdf` name from the uploaded file name (or URL tail)."""
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:120]
    if not name:
        return DEFAULT_PDF_NAME
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def source_key(paper_id: uuid.UUID | str, filename: str | None) -> str:
    return f"{paper_prefix(paper_id)}{safe_file_name(filename)}"


def source_file_name(s3_object_key: str) -> str:
    """The (sanitized) upload file name back from a source key — a hint for
    the metadata stage (arXiv ids / DOIs are often in file names)."""
    return PurePosixPath(s3_object_key).name


def preview_key(paper_id: uuid.UUID | str) -> str:
    return f"{paper_prefix(paper_id)}preview.png"


def figure_key(paper_id: uuid.UUID | str, figure_id: uuid.UUID | str) -> str:
    return f"{paper_prefix(paper_id)}figures/{figure_id}.png"


def public_url(s3: "S3Service", key: str) -> str:
    """The object's public URL (`S3_PUBLIC_BASE_URL`), as stored in
    `papers.file_url` / `papers.preview_url`."""
    return s3._public_url(key)


# -- object I/O (blocking; see the async wrappers below) --------------------------


def _translated(action: str, key: str, fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ClientError as exc:
        meta = exc.response.get("ResponseMetadata", {})
        status = int(meta.get("HTTPStatusCode") or 0)
        code = exc.response.get("Error", {}).get("Code", "")
        message = f"S3 {action} {key} failed: {code or status} {exc}"
        if status >= 500 or status in (408, 429) or code == "SlowDown":
            raise TemporaryError(message) from exc
        raise PermanentError(message) from exc
    except BotoCoreError as exc:  # endpoint unreachable, read timeout, ...
        raise TemporaryError(f"S3 {action} {key} failed: {exc}") from exc


def put_bytes(s3: "S3Service", key: str, body: bytes, content_type: str) -> None:
    _translated(
        "upload",
        key,
        lambda: s3.s3_client.put_object(
            Bucket=s3.bucket_name, Key=key, Body=body, ContentType=content_type
        ),
    )


def get_bytes(s3: "S3Service", key: str) -> bytes:
    return _translated("download", key, lambda: s3.get_object_bytes(key))


def delete_key(s3: "S3Service", key: str) -> None:
    _translated(
        "delete",
        key,
        lambda: s3.s3_client.delete_object(Bucket=s3.bucket_name, Key=key),
    )


def delete_prefix(s3: "S3Service", prefix: str) -> int:
    """Delete every object under `prefix`; returns how many."""
    deleted = 0

    def run() -> None:
        nonlocal deleted
        paginator = s3.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=s3.bucket_name, Prefix=prefix):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                s3.s3_client.delete_objects(
                    Bucket=s3.bucket_name, Delete={"Objects": keys, "Quiet": True}
                )
                deleted += len(keys)

    _translated("delete", prefix, run)
    return deleted


def delete_paper_objects(s3: "S3Service", paper_id: uuid.UUID | str) -> int:
    """Delete everything under `papers/{paper_id}/`; returns how many."""
    return delete_prefix(s3, paper_prefix(paper_id))


def key_from_public_url(s3: "S3Service", url: str | None) -> str | None:
    """The object key behind one of our public URLs (`preview_url`), or None
    for URLs that aren't ours."""
    base = public_url(s3, "")
    if url and url.startswith(base) and len(url) > len(base):
        return url[len(base) :].split("?", 1)[0]
    return None


# -- for stages -----------------------------------------------------------------


def _paper_pdf_key(ctx: "StageContext") -> str:
    from app.database.models import Paper

    with ctx.read_session() as session:
        key = (
            session.query(Paper.s3_object_key).filter(Paper.id == ctx.paper_id).scalar()
        )
    if not key:
        from app.ingest.stages.base import fail_permanent

        fail_permanent("The paper has no stored PDF (papers.s3_object_key is empty).")
    return str(key)


async def load_pdf(ctx: "StageContext") -> bytes:
    """The paper's PDF bytes from S3 (for text_layer / preview / figures)."""
    key = await asyncio.to_thread(_paper_pdf_key, ctx)
    return await asyncio.to_thread(get_bytes, ctx.get_s3(), key)


async def upload(ctx: "StageContext", key: str, body: bytes, content_type: str) -> str:
    """Upload `body` to `key`; returns its public URL."""
    s3 = ctx.get_s3()
    await asyncio.to_thread(put_bytes, s3, key, body, content_type)
    return public_url(s3, key)
