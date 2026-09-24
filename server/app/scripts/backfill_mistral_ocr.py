"""
Backfill Mistral OCR onto existing papers.

For each paper where parser IS NULL OR parser = 'pymupdf':
  1. Calls the jobs-api `/ocr/run` endpoint (synchronous Mistral OCR +
     high-DPI figure re-render against the paper's existing S3 PDF)
  2. Updates the paper row with parser='mistral', ocr jsonb, figure_count,
     page_count.

Idempotent: skips papers that already have ocr populated and figures rendered.

Usage:
    python -m app.scripts.backfill_mistral_ocr [--dry-run] [--limit N] [--paper-id UUID]

The jobs-api is reached via CELERY_API_URL (the same env var the rest of the
server uses). MISTRAL_API_KEY must be set on the jobs container, not the server.
"""

import argparse
import logging
import os
import sys
import time
import uuid
from typing import Any, Dict, Optional

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from sqlalchemy import update

from app.database.database import SessionLocal
from app.database.models import Paper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


JOBS_API_URL = os.environ.get("CELERY_API_URL", "http://jobs-api:8001")


def _call_ocr_run(s3_key: str, paper_id: str) -> Optional[Dict[str, Any]]:
    """Call the jobs-api /ocr/run endpoint with retry-on-429 backoff."""
    payload = {
        "s3_object_key": s3_key,
        # Stable per-paper prefix so re-runs overwrite rather than duplicate.
        "key_prefix": f"figures/{paper_id}",
    }
    delay = 2.0
    for attempt in range(5):
        try:
            resp = requests.post(
                f"{JOBS_API_URL.rstrip('/')}/ocr/run",
                json=payload,
                timeout=600,
            )
        except requests.RequestException as e:
            logger.warning("OCR call network error (attempt %d): %s", attempt + 1, e)
            time.sleep(delay)
            delay *= 2
            continue

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After") or delay)
            logger.warning(
                "Got 429 from jobs-api, sleeping %.1fs (attempt %d)",
                retry_after,
                attempt + 1,
            )
            time.sleep(retry_after)
            delay *= 2
            continue
        logger.error(
            "Jobs-api /ocr/run returned %s: %s",
            resp.status_code,
            resp.text[:500],
        )
        return None

    logger.error("Exhausted retries for %s", paper_id)
    return None


def backfill(
    dry_run: bool = False,
    limit: Optional[int] = None,
    paper_id_filter: Optional[str] = None,
) -> None:
    db = SessionLocal()
    try:
        q = db.query(Paper).filter(
            (Paper.parser.is_(None)) | (Paper.parser == "pymupdf")
        )
        if paper_id_filter:
            q = q.filter(Paper.id == uuid.UUID(paper_id_filter))
        if limit:
            q = q.limit(limit)
        papers = q.all()

        logger.info("Found %d papers to backfill", len(papers))
        succeeded = 0
        failed = 0

        for i, paper in enumerate(papers, start=1):
            pid = str(paper.id)
            s3_key = paper.s3_object_key
            if not s3_key:
                logger.warning("Skipping %s: no s3_object_key", pid)
                failed += 1
                continue

            logger.info(
                "[%d/%d] Backfilling %s (s3=%s)",
                i,
                len(papers),
                pid,
                s3_key,
            )

            if dry_run:
                continue

            t0 = time.time()
            result = _call_ocr_run(str(s3_key), pid)
            elapsed = time.time() - t0
            if not result or not result.get("success"):
                logger.error(
                    "OCR failed for %s: %s",
                    pid,
                    (result or {}).get("error", "no result"),
                )
                failed += 1
                continue

            db.execute(
                update(Paper)
                .where(Paper.id == paper.id)
                .values(
                    parser="mistral",
                    ocr=result.get("ocr"),
                    generated_outline=None,
                    figure_count=result.get("figure_count"),
                    page_count=result.get("page_count"),
                    raw_content=result.get("raw_content"),
                    page_offset_map=result.get("page_offset_map"),
                )
            )
            db.commit()
            succeeded += 1
            logger.info(
                "  ok in %.1fs (figures=%s pages=%s)",
                elapsed,
                result.get("figure_count"),
                result.get("page_count"),
            )

        logger.info(
            "Backfill complete: %d succeeded, %d failed",
            succeeded,
            failed,
        )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Mistral OCR on existing papers")
    parser.add_argument("--dry-run", action="store_true", help="List papers without re-OCR'ing")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of papers")
    parser.add_argument("--paper-id", type=str, default=None, help="Backfill a single paper by id")
    args = parser.parse_args()

    backfill(
        dry_run=args.dry_run,
        limit=args.limit,
        paper_id_filter=args.paper_id,
    )
