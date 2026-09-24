"""`Settings` keeps the defaults and empty-value handling of the old
`os.getenv` reads."""

from app.settings import Settings


def test_empty_falls_back_where_getenv_or_default_did(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "")
    monkeypatch.setenv("MISTRAL_OCR_BATCH_PAGES", "")
    monkeypatch.setenv("MISTRAL_API_KEY", "  ")
    monkeypatch.setenv("CONTACT_EMAIL", " me@example.org ")
    settings = Settings()
    assert settings.OPENAI_MODEL == "gpt-5.5"
    assert settings.MISTRAL_OCR_BATCH_PAGES == 16
    assert settings.MISTRAL_API_KEY is None
    assert settings.CONTACT_EMAIL == "me@example.org"


def test_empty_is_kept_where_getenv_default_did(monkeypatch):
    monkeypatch.setenv("CODEX_PROXY_MODEL", "")
    monkeypatch.delenv("DEFAULT_LLM_PROVIDER", raising=False)
    settings = Settings()
    assert settings.CODEX_PROXY_MODEL == ""
    assert settings.DEFAULT_LLM_PROVIDER == "openai"


def test_flags_parse_like_before(monkeypatch):
    monkeypatch.setenv("SECURE_COOKIES", "TRUE")
    monkeypatch.setenv("AZURE_OPENAI", " yes ")
    monkeypatch.setenv("ADMIN_EMAILS", "A@x.org, ,b@y.org")
    settings = Settings()
    assert settings.secure_cookies is True
    assert settings.azure_openai is True
    assert settings.admin_emails == {"a@x.org", "b@y.org"}
