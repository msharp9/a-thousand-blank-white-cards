"""tbwc.app — FastAPI application factory.

Exposes `app` (the ASGI application) and `create_app()` for testing.
REST game routes and /ws are mounted by later phases.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tbwc.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup → yield → shutdown.

    Later phases will:
      - warm up the LangGraph agent
      - open the room registry
    """
    # startup
    try:
        from tbwc.rag.seed import load_seed_cards

        load_seed_cards()
    except Exception:  # pragma: no cover - startup best-effort; missing key/network
        logger.exception("Seed card loading failed at startup")
    yield
    # shutdown


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    application = FastAPI(
        title="1000 Blank White Cards",
        description="AI-assisted party card game server.",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe — returns {"status": "ok"}."""
        return {"status": "ok"}

    return application


# Module-level app instance used by uvicorn and tests.
app = create_app()
