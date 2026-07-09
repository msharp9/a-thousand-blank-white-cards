"""tbwc.app — FastAPI application factory.

Exposes `app` (the ASGI application) and `create_app()` for testing.
REST game routes and /ws are mounted by later phases.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tbwc.config import get_settings
from tbwc.rooms.manager import room_manager
from tbwc.ws import router as ws_router

logger = logging.getLogger(__name__)


class CreateRoomResponse(BaseModel):
    code: str


class JoinRoomRequest(BaseModel):
    name: str


class JoinRoomResponse(BaseModel):
    code: str
    player_id: str


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

    # startup: log LangSmith tracing status
    _settings = get_settings()
    if _settings.langsmith_tracing:
        logger.info(
            "LangSmith tracing ENABLED project=%s endpoint=%s",
            _settings.langsmith_project,
            _settings.langsmith_endpoint,
        )
    else:
        logger.warning("LangSmith tracing DISABLED — set LANGSMITH_TRACING=true to enable")
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

    @application.post("/rooms", response_model=CreateRoomResponse, tags=["rooms"])
    async def create_room() -> CreateRoomResponse:
        """Create a new game room. Returns a 6-char join code."""
        code = room_manager.create_room()
        return CreateRoomResponse(code=code)

    @application.post("/rooms/{code}/join", response_model=JoinRoomResponse, tags=["rooms"])
    async def join_room(code: str, body: JoinRoomRequest) -> JoinRoomResponse:
        """Register a player in a room. Returns player_id for localStorage/reconnect."""
        result = room_manager.join(code, body.name)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Room '{code}' not found")
        room_code, player_id = result
        return JoinRoomResponse(code=room_code, player_id=player_id)

    application.include_router(ws_router)

    return application


# Module-level app instance used by uvicorn and tests.
app = create_app()
