"""The server's (and the ingest worker's) configuration, from the environment.

`get_settings()` is the one place the app reads its env. `server/.env` is
loaded into `os.environ` first (variables already set in the environment
win, as with `docker compose`'s `env_file`), which also serves the SDKs that
read their own variables (Logfire's `LOGFIRE_TOKEN`, ...).

Defaults and parsing match what the scattered `os.getenv` calls did: where
an empty value used to fall back to the default (`os.getenv(X) or d`) the
field is `_EmptyIsDefault`; elsewhere an empty string is kept as is.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Optional

from dotenv import load_dotenv
from pydantic import BeforeValidator
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(SERVER_ROOT / ".env")


def _empty_is_default(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        raise PydanticUseDefault()
    return value.strip() if isinstance(value, str) else value


# An unset, empty or blank variable means the default; values are stripped.
_EmptyIsDefault = BeforeValidator(_empty_is_default)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

    # -- database / deployment -------------------------------------------
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/annotated-paper"
    CLIENT_DOMAIN: str = "http://localhost:3000"
    PORT: int = 8000  # `python -m app.main` only; gunicorn has its own config
    MAX_UPLOAD_SIZE_MB: int = 50

    # -- auth --------------------------------------------------------------
    SESSION_COOKIE_DOMAIN: Optional[str] = None
    SECURE_COOKIES: str = "false"
    ADMIN_EMAILS: str = ""  # comma-separated
    RESEND_API_KEY: Optional[str] = None

    # -- S3 / MinIO --------------------------------------------------------
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: Optional[str] = None
    CLOUDFLARE_BUCKET_NAME: Optional[str] = None
    S3_ENDPOINT_URL: Optional[str] = None
    S3_PUBLIC_BASE_URL: Optional[str] = None

    # Companion-repo snapshots; unset = `server/.repo_snapshots`.
    REPO_STORAGE_DIR: Optional[str] = None

    # -- LLM providers (see app.llm.model_registry) ------------------------
    DEFAULT_LLM_PROVIDER: str = "openai"
    # JSON object of per-model capability overrides.
    MODEL_OVERRIDES: Optional[str] = None

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    OPENAI_MODEL: Annotated[str, _EmptyIsDefault] = "gpt-5.5"
    OPENAI_FAST_MODEL: Annotated[str, _EmptyIsDefault] = "gpt-5.4-mini"
    OPENAI_MODELS: Optional[str] = None  # the picker's list, "id|Name,..."
    AZURE_OPENAI: str = ""  # "1" / "true" / "yes" routes OpenAI to Azure
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    AZURE_OPENAI_API_VERSION: str = "2025-04-01-preview"

    CODEX_PROXY_BASE_URL: Optional[str] = None
    CODEX_PROXY_API_KEY: str = "codex-proxy-local"
    CODEX_PROXY_MODEL: str = "gpt-5.5"
    CODEX_PROXY_FAST_MODEL: str = "gpt-5.6-luna"
    CODEX_PROXY_MODELS: Optional[str] = None

    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: Annotated[str, _EmptyIsDefault] = "claude-sonnet-5"
    ANTHROPIC_FAST_MODEL: Annotated[str, _EmptyIsDefault] = "claude-haiku-4-5"
    ANTHROPIC_MODELS: Optional[str] = None

    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: Annotated[str, _EmptyIsDefault] = "gemini-3.7-flash"
    GEMINI_FAST_MODEL: Annotated[str, _EmptyIsDefault] = "gemini-3.7-flash"
    GEMINI_MODELS: Optional[str] = None

    # -- ingest ------------------------------------------------------------
    MISTRAL_API_KEY: Annotated[Optional[str], _EmptyIsDefault] = None
    MISTRAL_OCR_ENDPOINT: Annotated[str, _EmptyIsDefault] = (
        "https://api.mistral.ai/v1/ocr"
    )
    MISTRAL_OCR_MODEL: Annotated[str, _EmptyIsDefault] = "mistral-ocr-4-1"
    MISTRAL_OCR_BATCH_PAGES: Annotated[int, _EmptyIsDefault] = 16
    OPENAI_OCR_REPAIR_DPI: int = 220

    # -- outbound APIs -----------------------------------------------------
    # Crossref / OpenAlex polite pool (app.core.http).
    CONTACT_EMAIL: Annotated[Optional[str], _EmptyIsDefault] = None
    OPENALEX_API_KEY: Annotated[Optional[str], _EmptyIsDefault] = None
    SEMANTIC_SCHOLAR_API_KEY: Optional[str] = None
    EXA_API_KEY: Optional[str] = None

    # -- derived -----------------------------------------------------------
    @property
    def secure_cookies(self) -> bool:
        return self.SECURE_COOKIES.lower() == "true"

    @property
    def azure_openai(self) -> bool:
        return self.AZURE_OPENAI.strip().lower() in ("1", "true", "yes")

    @property
    def admin_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.ADMIN_EMAILS.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    """The settings, read once per process (tests clear the cache)."""
    return Settings()
