"""tbwc.app — FastAPI application factory.

Exposes `app` (the ASGI application) and `create_app()` for testing.
`create_app()` mounts the REST routes (GET /health, POST /rooms,
POST /rooms/{code}/join) and includes the WebSocket router that serves live
gameplay at /ws/{room_code}. FastAPI/OpenAPI does not document WebSocket routes,
so the /ws protocol is described in the app's OpenAPI `description` (rendered at
the top of /docs) and in the project README.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tbwc.config import get_settings, require_openai_api_key
from tbwc.rooms.manager import check_single_worker, room_manager
from tbwc.ws import router as ws_router

logger = logging.getLogger(__name__)


# Rendered at the top of /docs. WebSocket routes are never included in the
# OpenAPI schema by FastAPI, so the live-gameplay /ws protocol is documented here.
WS_PROTOCOL_DESCRIPTION = """\
AI-assisted party card game server.

## WebSocket API — live gameplay

Realtime play happens over a WebSocket, which is **not** listed among the REST
routes below because FastAPI/OpenAPI does not document WebSocket endpoints.

**Endpoint:** `ws://<host>/ws/{room_code}` (use `wss://` in production).

**Handshake.** Create a room with `POST /rooms`, register a player with
`POST /rooms/{code}/join` (returns a `player_id`), then open the socket. The
**first message must be a `join`** envelope carrying that `player_id`; any other
first message closes the socket. On connect (and reconnect) the server replies
with a full `state` snapshot. All messages are JSON objects with a `type` field.

### Client → server messages

| type | fields | purpose |
| --- | --- | --- |
| `join` | `player_id` (null on first join), `name` | Authenticate the socket into the room; must be the first message. |
| `start` | — | Build/shuffle the deck, deal starting hands, begin play. |
| `play` | `card_id`, `placement` (`zone`, `target_player_id`), `chosen_player_id?`, `chosen_card_id?`, `title?`, `description?` | Play a card; the AI referee interprets it and applies the effect (active player only). Ends the turn. For a BLANK card, the first play carries the authored `title`+`description` (the card is filled in and persisted before interpretation); a prompt_choice follow-up re-sends only `card_id`+the choice. |
| `pass` | — | End your turn without playing a card (active player only). Drawing is automatic at turn start, so there is no manual `draw`. |
| `create_card` | `title`, `description` | Author a new card and interpret it immediately (allowed off-turn). |
| `preview_card` | `title`, `description` | Dry-run interpretation preview without changing state. |
| `epilogue_vote` | `card_id`, `keep` | Vote to keep/discard a card during the epilogue phase. |

### Server → client messages

| type | fields | meaning |
| --- | --- | --- |
| `state` | `state` | Full game-state snapshot (sent on connect and after every mutation). |
| `brewing` | `card_id` | The referee is interpreting a card (in-flight indicator). |
| `card_interpreted` | `card_id`, `program`, `snippet`, `verdict` | Result of interpreting a played/created card. |
| `effect_applied` | `log_entry` | An effect was applied; human-readable log line. |
| `preview_result` | `program`, `snippet`, `verdict` | Reply to `preview_card`. |
| `prompt_choice` | `card_id`, `prompt`, `choices` | Server asks the active player to pick a target. |
| `epilogue` | `cards` | Epilogue phase opened with the cards created this game. |
| `error` | `message` | An error (bad message, not your turn, room not found, …). |

Close codes: `4000` bad handshake, `4001` unknown `player_id`, `4004` room not
found, `4009` connection replaced by a newer socket for the same player.
"""


class CreateRoomRequest(BaseModel):
    # Room mode chosen by the host at creation. Optional so old clients that
    # POST /rooms with no body still work (defaults to "both").
    mode: Literal["online", "in_person", "both"] = "both"


class CreateRoomResponse(BaseModel):
    code: str


class JoinRoomRequest(BaseModel):
    name: str


class JoinRoomResponse(BaseModel):
    code: str
    player_id: str
    # True when the game had already started at join time, so this joiner was
    # seated as a spectator (no turn, cannot author/play). The client can also
    # read its own PlayerSnapshot.spectator from the state snapshot; this field
    # just surfaces it immediately at REST-join time.
    spectator: bool = False


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup → yield → shutdown.

    Later phases will:
      - warm up the LangGraph agent
      - open the room registry
    """
    # startup: fail fast with an actionable message if the OpenAI key is missing.
    # require_openai_api_key() is a no-op when llm_provider == "ollama" (the local
    # OpenAI-compatible backend ignores the key), so this gate only fires for OpenAI.
    require_openai_api_key()

    # startup: warn loudly if a multi-worker deployment is configured — the
    # room registry uses a process-local in-memory store (single-worker only).
    check_single_worker()

    try:
        from tbwc.rag.seed import load_seed_cards

        load_seed_cards()
    except Exception:  # pragma: no cover - startup best-effort; network/store errors
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
        description=WS_PROTOCOL_DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "meta", "description": "Liveness/health probes."},
            {"name": "rooms", "description": "Create rooms and register players (REST)."},
            {
                "name": "websocket",
                "description": (
                    "Live gameplay over `ws://<host>/ws/{room_code}`. Not shown as a "
                    "route (OpenAPI omits WebSocket endpoints) — see the description above."
                ),
            },
        ],
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
    async def create_room(body: CreateRoomRequest | None = None) -> CreateRoomResponse:
        """Create a new game room. Returns a 6-char join code.

        The request body is optional: a POST with no body defaults the room mode
        to "both" so older clients keep working.
        """
        mode = body.mode if body else "both"
        code = room_manager.create_room(mode=mode)
        return CreateRoomResponse(code=code)

    @application.post("/rooms/{code}/join", response_model=JoinRoomResponse, tags=["rooms"])
    async def join_room(code: str, body: JoinRoomRequest) -> JoinRoomResponse:
        """Register a player in a room. Returns player_id for localStorage/reconnect."""
        result = room_manager.join(code, body.name)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Room '{code}' not found")
        room_code, player_id, spectator = result
        return JoinRoomResponse(code=room_code, player_id=player_id, spectator=spectator)

    application.include_router(ws_router)

    return application


# Module-level app instance used by uvicorn and tests.
app = create_app()
