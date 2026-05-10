"""OpenAI vision OCR repair for pages where Mistral OCR diverges badly."""

import base64
import json
import logging
import os
import random
import time
from typing import Any, Dict, Optional

import openai

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_OCR_MODEL = "gpt-5.4-mini"
OPENAI_OCR_MAX_RETRIES = int(os.environ.get("OPENAI_OCR_MAX_RETRIES", "2"))
OPENAI_OCR_INITIAL_BACKOFF = float(os.environ.get("OPENAI_OCR_INITIAL_BACKOFF", "1.0"))
OPENAI_OCR_TIMEOUT = float(os.environ.get("OPENAI_OCR_TIMEOUT", "120"))


def _is_azure_openai_enabled() -> bool:
    return os.getenv("AZURE_OPENAI", "").strip().lower() in ("1", "true", "yes")


def _is_openai_compatible_azure_endpoint(url: Optional[str]) -> bool:
    return bool(url) and url.rstrip("/").endswith("/openai/v1")


class OpenAIOCRUnavailable(Exception):
    """Raised when OpenAI OCR repair is unavailable or fails."""


class OpenAIOCRClient:
    """Small synchronous client for OCRing one rendered page image."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("OPENAI_OCR_MODEL") or DEFAULT_OPENAI_OCR_MODEL
        self.base_url = os.environ.get("OPENAI_BASE_URL") or None
        self.is_azure = _is_azure_openai_enabled() and not self.base_url

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _create_client(self) -> openai.OpenAI | openai.AzureOpenAI:
        if not self.api_key:
            raise OpenAIOCRUnavailable("OPENAI_API_KEY not set")

        if self.is_azure:
            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
            if not endpoint:
                raise OpenAIOCRUnavailable(
                    "AZURE_OPENAI=true requires AZURE_OPENAI_ENDPOINT"
                )
            if _is_openai_compatible_azure_endpoint(endpoint):
                return openai.OpenAI(
                    api_key=self.api_key,
                    base_url=endpoint,
                    timeout=OPENAI_OCR_TIMEOUT,
                )
            return openai.AzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=endpoint,
                api_version=os.environ.get(
                    "AZURE_OPENAI_API_VERSION", "2025-04-01-preview"
                ),
                timeout=OPENAI_OCR_TIMEOUT,
            )

        return openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=OPENAI_OCR_TIMEOUT,
        )

    def ocr_page_png(self, png_bytes: bytes, page_number: int) -> Dict[str, Any]:
        """Return a Mistral-like page subset: index, markdown, model."""
        if not self.is_configured:
            raise OpenAIOCRUnavailable("OPENAI_API_KEY not set")

        encoded = base64.b64encode(png_bytes).decode("ascii")
        client = self._create_client()

        last_err: Optional[Exception] = None
        for attempt in range(OPENAI_OCR_MAX_RETRIES + 1):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "OCR this PDF page into markdown. Preserve reading "
                                        "order, headings, paragraphs, footnotes, tables, "
                                        "equations, figure captions, and image placeholders "
                                        "when visible. Return only JSON matching the schema."
                                    ),
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{encoded}",
                                        "detail": "high",
                                    },
                                },
                            ],
                        }
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "ocr_page",
                            "strict": True,
                            "schema": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "markdown": {
                                        "type": "string",
                                        "description": "OCR markdown for the page.",
                                    }
                                },
                                "required": ["markdown"],
                            },
                        },
                    },
                )
                content = response.choices[0].message.content if response.choices else None
                if not content:
                    raise OpenAIOCRUnavailable("OpenAI returned no OCR content")
                parsed = json.loads(content)
                return {
                    "index": page_number - 1,
                    "markdown": str(parsed.get("markdown") or "").replace("\x00", ""),
                    "model": self.model,
                }
            except (
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.APIStatusError,
                openai.RateLimitError,
                json.JSONDecodeError,
                OpenAIOCRUnavailable,
            ) as e:
                last_err = e
                if attempt >= OPENAI_OCR_MAX_RETRIES:
                    break
                delay = OPENAI_OCR_INITIAL_BACKOFF * (2**attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "OpenAI OCR page %s failed on attempt %s/%s: %s; retrying in %.2fs",
                    page_number,
                    attempt + 1,
                    OPENAI_OCR_MAX_RETRIES + 1,
                    e,
                    delay,
                )
                time.sleep(delay)

        raise OpenAIOCRUnavailable(
            f"OpenAI OCR failed for page {page_number}: {last_err}"
        )


openai_ocr_client = OpenAIOCRClient()
