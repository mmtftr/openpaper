"""Ingest configuration that isn't an LLM model slot.

- Worker knobs (concurrency per resource, poll interval, heartbeat).
- The OCR service (`ingest.ocr` in the design): Mistral OCR is a document
  API with its own key and model name, not a chat model, so it is plain env
  config here rather than a row in `app.llm.model_slots`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

from app.core.errors import ConfigError


class Resource(StrEnum):
    """What a stage mostly waits on; each has its own concurrency limit."""

    CPU = "cpu"  # pymupdf work, run in a process pool
    OCR = "ocr"  # Mistral OCR API
    LLM = "llm"  # chat-model calls
    NETWORK = "network"  # Crossref / OpenAlex / arXiv / S3


# Max stages running at once per resource (design §4).
CONCURRENCY: dict[Resource, int] = {
    Resource.CPU: 2,
    Resource.OCR: 2,
    Resource.LLM: 4,
    Resource.NETWORK: 8,
}

POLL_INTERVAL_SECONDS = 0.25
HEARTBEAT_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class OcrConfig:
    api_key: str | None
    endpoint: str
    model: str
    # Pages per OCR request; pages are saved as each batch lands.
    batch_pages: int = 16

    def require(self) -> "OcrConfig":
        """Raise `ConfigError` (stage fails at once, readable) when unusable."""
        if not self.api_key:
            raise ConfigError("MISTRAL_API_KEY is not set (server/.env)")
        return self


def ocr_config() -> OcrConfig:
    """Mistral OCR settings from the environment (same names as `jobs/`)."""
    return OcrConfig(
        api_key=(os.getenv("MISTRAL_API_KEY") or "").strip() or None,
        endpoint=os.getenv("MISTRAL_OCR_ENDPOINT") or "https://api.mistral.ai/v1/ocr",
        model=os.getenv("MISTRAL_OCR_MODEL") or "mistral-ocr-4-1",
        batch_pages=int(os.getenv("MISTRAL_OCR_BATCH_PAGES") or 16),
    )
