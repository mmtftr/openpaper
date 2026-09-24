import logging
import os

import logfire
import uvicorn  # type: ignore
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.annotation_api import annotation_router
from app.api.api import router
from app.api.auth_api import auth_router
from app.api.conversation_api import conversation_router
from app.api.discover_api import discover_router
from app.api.document_api import document_router
from app.api.errors import ERROR_RESPONSES, install_error_handlers
from app.api.highlight_api import highlight_router
from app.api.message_api import message_router
from app.api.paper_api import paper_router
from app.api.paper_figure_api import paper_figure_router
from app.api.paper_search_api import paper_search_router
from app.api.paper_tag_api import paper_tag_router
from app.api.paper_upload_api import paper_upload_router
from app.api.projects.project_papers_api import project_papers_router
from app.api.projects.projects_api import projects_router
from app.api.repo_api import repo_router
from app.api.search_api import search_router
from app.api.settings_api import settings_router
from app.api.webhook_api import webhook_router
from app.ingest.api import ingest_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

load_dotenv()

logfire.configure(
    service_name="openpaper-server",
    send_to_logfire="if-token-present",
)


def _safe_instrument(label: str, instrument) -> None:
    """Best-effort observability hookup.

    Each ``logfire.instrument_*`` pulls an optional OpenTelemetry integration
    whose version must line up with logfire's. A mismatch (e.g. an unpinned
    rebuild floats logfire ahead of an integration package) raises at import
    time — but instrumentation is observability, not core function, so it must
    not take down server boot. Log and continue.
    """
    try:
        instrument()
    except Exception as exc:  # noqa: BLE001 - telemetry must never break boot
        logger.warning("Skipping logfire instrumentation %s: %s", label, exc)


_safe_instrument("pydantic", lambda: logfire.instrument_pydantic(record="failure"))
_safe_instrument("openai", logfire.instrument_openai)
_safe_instrument("httpx", lambda: logfire.instrument_httpx(capture_all=True))

app = FastAPI(
    title="Open Paper",
    description="A web application for uploading and annotating papers.",
    version="1.0.0",
    responses=ERROR_RESPONSES,
)
install_error_handlers(app)
_safe_instrument(
    "fastapi", lambda: logfire.instrument_fastapi(app, capture_headers=True)
)

client_domain = os.getenv("CLIENT_DOMAIN", "http://localhost:3000")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[client_domain],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
    allow_credentials=True,  # This is required for cookies
    max_age=600,  # Cache preflight requests for 10 minutes
)

# Include the router in the main app
app.include_router(router, prefix="/api")
app.include_router(auth_router, prefix="/api/auth")  # Auth routes
app.include_router(paper_router, prefix="/api/paper")
app.include_router(conversation_router, prefix="/api/conversation")
app.include_router(message_router, prefix="/api/message")
app.include_router(highlight_router, prefix="/api/highlight")
app.include_router(annotation_router, prefix="/api/annotation")
app.include_router(projects_router, prefix="/api/projects")
app.include_router(project_papers_router, prefix="/api/projects/papers")
app.include_router(paper_search_router, prefix="/api/search/global")
app.include_router(search_router, prefix="/api/search/local")
app.include_router(paper_figure_router, prefix="/api/paper")
app.include_router(repo_router, prefix="/api/paper")
app.include_router(ingest_router, prefix="/api/paper")
app.include_router(paper_upload_router, prefix="/api/paper/upload")
app.include_router(paper_tag_router, prefix="/api/paper/tag")
app.include_router(webhook_router, prefix="/api/webhooks")  # Webhook routes
app.include_router(discover_router, prefix="/api/discover")
app.include_router(document_router, prefix="/api/document")
app.include_router(settings_router, prefix="/api/settings")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    log_config = uvicorn.config.LOGGING_CONFIG  # type: ignore
    log_config["formatters"]["access"]["fmt"] = (
        "%(asctime)s - %(levelname)s - %(message)s"
    )
    log_config["formatters"]["default"]["fmt"] = (
        "%(asctime)s - %(levelname)s - %(message)s"
    )
    # Set higher log level to see more details
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        # reload=True,
        log_level="debug",
        log_config=log_config,
        forwarded_allow_ips="*",  # Allow all forwarded IPs
        proxy_headers=True,  # Enable proxy headers
    )
