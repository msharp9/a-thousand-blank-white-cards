"""board.app — FastAPI application factory.

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

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import get_settings, warn_if_no_llm_credentials
from models.card import decode_card_art
from logging_config import configure_logging
from board.rooms.manager import check_single_worker, room_manager
from board.ws import router as ws_router

logger = logging.getLogger(__name__)

# Triage-agent telemetry is droppable, so shutdown only waits this long for
# in-flight failure-triage tasks before cancelling stragglers.
TRIAGE_DRAIN_TIMEOUT_SECONDS = 5.0


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

The turn model is **auto-draw → play → end turn**: at the start of each turn the
server automatically draws `rules.draw` card(s) for the new active player — there
is no client `draw` message. Drawing the last card arms end-of-game (the drawer
finishes their turn, then the game ends).

| type | fields | purpose |
| --- | --- | --- |
| `join` | `player_id` (null on first join), `name` | Authenticate the socket into the room; must be the first message. |
| `start` | — | From the lobby, build the shared premade pool and enter setup. A setup start is accepted only after every required card has a successful executable draft; setup normally auto-starts at that point. |
| `play` | `card_id`, `placement` (`zone`, `target_player_id`), `chosen_player_id?`, `chosen_card_id?`, `title?`, `description?`, `art?` | Play a card; the AI arbiter interprets it and applies the effect (active player only). Ends the turn. For a BLANK card, the first play carries the authored `title`+`description` (the card is filled in and persisted before interpretation) and optionally `art` (a PNG data-URL, stored out-of-band and served via `GET /rooms/{code}/cards/{card_id}/art`); a prompt_choice follow-up re-sends only `card_id`+the choice. |
| `pass` / `end_turn` | — | End your turn without playing a card (active player only). `end_turn` is an accepted alias for `pass`, handled identically. |
| `create_card` | `title`, `description`, `art?` | Fill one stable card slot during SETUP. The server immediately drafts executable mechanics in the background; only successful drafts count toward readiness. Rejected in any other phase — the only mid-game authoring is playing a blank (see `play`). |
| `redraft_card` | `card_id`, `title`, `description`, `art?` | Retry or revise one of the sender's failed setup drafts without consuming another card slot. Omitted art preserves the existing drawing. |
| `preview_card` | `title`, `description` | Dry-run interpretation preview without changing state (setup phase only, like `create_card`). |
| `interaction_response` | `schema_version`, `interaction_id`, typed `payload` | Submit one authenticated response to the active generic interaction. |
| `admin_propose` | `actions` | Host-only: propose an atomic, typed correction bundle during play or results. Gameplay pauses while every other seated player votes. |
| `admin_vote` | `proposal_id`, `accept` | Accept or reject the current host correction. One rejection or the 60-second deadline cancels it; unanimous acceptance applies it. |
| `admin_cancel` | `proposal_id` | Host-only: cancel the current correction proposal. |
| `epilogue_start` | — | Host-only: advance from results into the epilogue vote. |
| `epilogue_vote` | `card_id`, `keep` | Vote to keep/discard a card during the epilogue phase. |
| `epilogue_done` | — | Mark the player done voting; omitted cards count as abstentions. |
| `epilogue_finalize` | — | Host-only: finalize the epilogue immediately. |

### Server → client messages

| type | fields | meaning |
| --- | --- | --- |
| `state` | `state` | Full game-state snapshot (sent on connect and after every mutation). |
| `brewing` | `card_id` | The arbiter is interpreting a card (in-flight indicator). |
| `card_interpreted` | `card_id`, `program`, `snippet`, `verdict`, `comment`, `mechanical_status`, `mechanical_reason`, `correlation_id` | Result of interpreting a played card with durable mechanical diagnostics. |
| `effect_applied` | `log_entry` | An effect (or the arbiter's `comment`, prefixed `🤖`) was applied; human-readable log line. Also appended to `state.log` so it survives reconnect. |
| `preview_result` | `program`, `snippet`, `verdict`, `mechanical_status`, `mechanical_reason`, `correlation_id` | Real cloned-state interpretation and dry-run result. |
| `prompt_choice` | `card_id`, `prompt`, `choices` | Server asks the active player to pick a target. |
| `interaction_request` | `schema_version`, `interaction_id`, `descriptor`, `deadline_at`, safe `progress` | Versioned request delivered to one resolved audience member; replayed on reconnect. |
| `interaction_progress` | `schema_version`, `interaction_id`, `deadline_at`, safe `progress` | Counts-only barrier progress; never includes sealed response values. |
| `admin_proposal_result` | `proposal_id`, `outcome`, `reason?` | Reports whether a correction was applied, rejected, cancelled, or timed out. |
| `epilogue` | `cards` | Epilogue phase opened with the cards created this game. |
| `error` | `message` | An error (bad message, not your turn, room not found, …). |

Close codes: `4000` bad handshake, `4001` unknown `player_id`, `4004` room not
found, `4009` connection replaced by a newer socket for the same player.
"""


class CreateRoomRequest(BaseModel):
    # Room mode chosen by the host at creation. Optional so old clients that
    # POST /rooms with no body still work (defaults to "both").
    mode: Literal["online", "in_person", "both"] = "both"
    # Per-turn time limit in seconds (rules.turn_timer): the active player's
    # turn is force-ended when it runs out. None (the default) = no timer.
    turn_timer: int | None = Field(default=None, ge=1, le=3600)


class CreateRoomResponse(BaseModel):
    code: str


class JoinRoomRequest(BaseModel):
    name: str


class JoinRoomResponse(BaseModel):
    code: str
    player_id: str
    # True when the game had already started at join time, so this joiner was
    # seated as a spectator (no turn, cannot author/play) in the state
    # snapshot's separate `spectators` collection rather than `players`. This
    # field just surfaces that immediately at REST-join time.
    spectator: bool = False


class RoomSummary(BaseModel):
    code: str
    phase: Literal["lobby", "setup", "playing", "results", "epilogue", "ended"]
    mode: Literal["online", "in_person", "both"]
    player_count: int
    spectator_count: int
    # True while the room is still in the lobby (accepting new players, not
    # spectators). Mirrors the join policy in RoomManager.join.
    joinable: bool
    created_at: str


class ListRoomsResponse(BaseModel):
    rooms: list[RoomSummary]


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup → yield → shutdown.

    Later phases will:
      - warm up the LangGraph agent
      - open the room registry
    """
    # startup: install the central logging configuration first so every
    # subsequent startup log line (and all app loggers) use the shared format.
    configure_logging()

    # startup: SOFT credential check. A generic OpenAI-compatible gateway may be
    # keyless (local servers), so we never hard-fail here; we only log a warning
    # when the config can't possibly work (hosted OpenAI with no key). See
    # config.warn_if_no_llm_credentials.
    warn_if_no_llm_credentials()

    # startup: warn loudly if a multi-worker deployment is configured — the
    # room registry uses a process-local in-memory store (single-worker only).
    check_single_worker()
    room_manager.start_background_tasks()

    try:
        from agent.rag.seed import load_seed_cards

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

    if _settings.dev_mode:
        logger.warning("DEV_MODE enabled — room persistence and /dev endpoints are ACTIVE (do not use in production)")
    yield
    await room_manager.cancel_background_tasks()
    # shutdown
    from agent.triage import get_scheduler

    await get_scheduler().drain(timeout=TRIAGE_DRAIN_TIMEOUT_SECONDS)


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
        turn_timer = body.turn_timer if body else None
        code = room_manager.create_room(mode=mode, turn_timer=turn_timer)
        return CreateRoomResponse(code=code)

    @application.get("/rooms", response_model=ListRoomsResponse, tags=["rooms"])
    async def list_rooms(all: bool = False) -> ListRoomsResponse:
        """List active rooms, newest first.

        By default only ``joinable`` rooms (``phase == "lobby"``) are returned,
        matching what a "join a game" lobby screen wants. Pass ``?all=true`` to
        also see rooms that have already started (any phase except ``ended``).
        """
        rooms = [
            room
            for room in room_manager.list_rooms()
            if room.state.phase != "ended" and (all or room.state.phase == "lobby")
        ]
        summaries = [
            RoomSummary(
                code=room.code,
                phase=room.state.phase,
                mode=room.state.mode,
                player_count=len(room.state.players),
                spectator_count=len(room.state.spectators),
                joinable=room.state.phase == "lobby",
                created_at=room.created_at.isoformat(),
            )
            for room in rooms
        ]
        summaries.sort(key=lambda r: r.created_at, reverse=True)
        return ListRoomsResponse(rooms=summaries)

    @application.post("/rooms/{code}/join", response_model=JoinRoomResponse, tags=["rooms"])
    async def join_room(code: str, body: JoinRoomRequest) -> JoinRoomResponse:
        """Register a player in a room. Returns player_id for localStorage/reconnect."""
        result = room_manager.join(code, body.name)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Room '{code}' not found")
        room_code, player_id, spectator = result
        return JoinRoomResponse(code=room_code, player_id=player_id, spectator=spectator)

    @application.get("/rooms/{code}/state", tags=["rooms"])
    async def get_room_state(code: str) -> dict:
        """Debug/read-only snapshot of a room's game state, spectator view.

        Returns the JSON-serialisable GameState snapshot (room_code, players,
        phase, deck_count, cards, …). The endpoint is unauthenticated so it
        serves the fully-redacted view — hand contents and deck order never
        leave the server here. 404 if the room does not exist; never mutates
        state.
        """
        room = room_manager.get(code)
        if room is None:
            raise HTTPException(status_code=404, detail=f"Room '{code}' not found")
        return room.snapshot_for(None)

    @application.get("/rooms/{code}/cards/{card_id}/art", tags=["rooms"])
    async def get_card_art(code: str, card_id: str) -> Response:
        """Serve a card's hand-drawn art as PNG bytes, out-of-band from state.

        Art never rides the GameState snapshot (cards carry only ``has_art``);
        clients fetch the image here instead. Card ids are immutable and art is
        keyed by card id plus setup revision on the client, so the response is
        served with long-lived immutable cache headers. 404 when the room does
        not exist or the card has no art.
        """
        room = room_manager.get(code)
        if room is None:
            raise HTTPException(status_code=404, detail=f"Room '{code}' not found")
        data_url = room.card_art.get(card_id)
        if data_url is None:
            raise HTTPException(status_code=404, detail=f"Card '{card_id}' has no art")
        png = decode_card_art(data_url)
        return Response(
            content=png,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.post("/rooms/{code}/dev/skip-setup", tags=["rooms"])
    async def dev_skip_setup(code: str) -> dict:
        """DEV-ONLY shortcut: fast-forward a room to ``phase="playing"``.

        Enters setup (if needed), waits for already-submitted card drafts, keeps
        successful ones, and fills every missing/failed authoring slot with a
        blank card so play can begin without fake mechanics. Returns the resulting
        UNREDACTED state snapshot (dev tooling wants hands/deck; the endpoint does
        not exist in production). Only active when ``dev_mode`` is set.
        """
        # 404 (not 403) when dev mode is off so the endpoint's very existence
        # stays hidden in production.
        if not get_settings().dev_mode:
            raise HTTPException(status_code=404, detail="Not found")
        room = room_manager.get(code)
        if room is None:
            raise HTTPException(status_code=404, detail=f"Room '{code}' not found")
        try:
            await room.dev_autofill_authoring()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return room.snapshot()

    @application.post("/rooms/{code}/dev/end-game", tags=["rooms"])
    async def dev_end_game(code: str) -> dict:
        """DEV-ONLY: force the current game to end now (real end-game path); opens the
        epilogue when real players remain, else ends. Only active when dev_mode is set.
        """
        # 404 (not 403) when dev mode is off so the endpoint's very existence
        # stays hidden in production.
        if not get_settings().dev_mode:
            raise HTTPException(status_code=404, detail="Not found")
        room = room_manager.get(code)
        if room is None:
            raise HTTPException(status_code=404, detail=f"Room '{code}' not found")
        try:
            await room.dev_force_end_game()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return room.snapshot()

    application.include_router(ws_router)

    return application


# Module-level app instance used by uvicorn and tests.
app = create_app()
