"""
Thin client around Mistral's direct OCR endpoint.

Direct Mistral (api.mistral.ai/v1/ocr with mistral-ocr-latest) parses an
18-page paper in ~1.7s and a 96-page paper in ~8s. Same model on Azure's
serverless gateway is ~250x slower and times out around 5 pages — that's why
we hit Mistral directly instead of going through Azure.
"""

import base64
import json
import logging
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://api.mistral.ai/v1/ocr"
DEFAULT_MODEL = "mistral-ocr-latest"

# Mistral imposes a per-key RPS cap on OCR. Tunable via env to avoid bumping
# into it during the backfill.
MAX_RETRIES = int(os.environ.get("MISTRAL_OCR_MAX_RETRIES", "4"))
INITIAL_BACKOFF_SECS = float(os.environ.get("MISTRAL_OCR_INITIAL_BACKOFF", "2.0"))
REQUEST_TIMEOUT_SECS = int(os.environ.get("MISTRAL_OCR_TIMEOUT", "300"))


class MistralOCRUnavailable(Exception):
    """Raised when Mistral OCR is not configured or all retries are exhausted."""


class MistralOCRClient:
    """Synchronous Mistral OCR client. Raises MistralOCRUnavailable on terminal
    failures so the caller can fall back to the local pymupdf path."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("MISTRAL_API_KEY")
        self.endpoint = endpoint or os.environ.get(
            "MISTRAL_OCR_ENDPOINT", DEFAULT_ENDPOINT
        )
        self.model = model or os.environ.get("MISTRAL_OCR_MODEL", DEFAULT_MODEL)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def ocr_pdf(self, pdf_path: str) -> Dict[str, Any]:
        """Run OCR on a local PDF file and return the parsed JSON response.

        Raises MistralOCRUnavailable when the key is missing or after retries
        are exhausted on transient failures.
        """
        if not self.is_configured:
            raise MistralOCRUnavailable("MISTRAL_API_KEY not set")

        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        pdf_bytes = path.read_bytes()
        return self.ocr_pdf_bytes(pdf_bytes)

    def ocr_pdf_bytes(self, pdf_bytes: bytes) -> Dict[str, Any]:
        if not self.is_configured:
            raise MistralOCRUnavailable("MISTRAL_API_KEY not set")

        b64 = base64.b64encode(pdf_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "document": {
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{b64}",
            },
            "include_image_base64": True,
        }
        body = json.dumps(payload).encode("utf-8")

        last_err: Optional[Exception] = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    self.endpoint,
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                )
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECS) as resp:
                    raw = resp.read()
                elapsed = time.time() - t0
                logger.info(
                    "mistral ocr ok (model=%s pages~? elapsed=%.2fs bytes=%d)",
                    self.model,
                    elapsed,
                    len(raw),
                )
                return json.loads(raw)
            except urllib.error.HTTPError as e:
                # 429 + 5xx are retryable; everything else is not.
                err_body = ""
                try:
                    err_body = e.read().decode("utf-8", errors="replace")[:1000]
                except Exception:
                    pass
                logger.warning(
                    "mistral ocr HTTP %s on attempt %d/%d: %s — %s",
                    e.code,
                    attempt + 1,
                    MAX_RETRIES + 1,
                    e.reason,
                    err_body,
                )
                last_err = e
                if e.code == 429 or 500 <= e.code < 600:
                    self._sleep_with_backoff(attempt, e)
                    continue
                raise MistralOCRUnavailable(
                    f"Mistral OCR HTTP {e.code}: {e.reason}"
                ) from e
            except (urllib.error.URLError, TimeoutError) as e:
                logger.warning(
                    "mistral ocr network error on attempt %d/%d: %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    e,
                )
                last_err = e
                self._sleep_with_backoff(attempt, e)
                continue

        raise MistralOCRUnavailable(
            f"Mistral OCR retries exhausted ({MAX_RETRIES + 1} attempts): {last_err}"
        )

    def _sleep_with_backoff(self, attempt: int, err: Exception) -> None:
        # Honor Retry-After if present on HTTPError.
        retry_after = None
        if isinstance(err, urllib.error.HTTPError):
            retry_after_header = err.headers.get("Retry-After") if err.headers else None
            if retry_after_header:
                try:
                    retry_after = float(retry_after_header)
                except ValueError:
                    retry_after = None

        delay = retry_after if retry_after is not None else (
            INITIAL_BACKOFF_SECS * (2**attempt) + random.uniform(0, 0.5)
        )
        time.sleep(delay)


mistral_ocr_client = MistralOCRClient()
