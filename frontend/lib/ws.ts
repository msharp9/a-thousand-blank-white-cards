"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  CardSnapshot,
  ClientMsg,
  GameStateSnapshot,
  InteractionProgressMsg,
  InteractionRequestMsg,
  PreviewResult,
  PromptChoiceMsg,
  ReactionResultMsg,
  ServerMsg,
} from "./types";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

// How long a transient (message-level) error banner stays up before it
// auto-dismisses. Kept short so a stale validation notice never lingers.
const TRANSIENT_ERROR_MS = 4500;

// Player identity is scoped per-room AND per-tab.
//
// We use sessionStorage (not localStorage) keyed by room code so that:
//   - a reload of the SAME tab keeps its player_id -> reconnects to the same
//     seat (sessionStorage survives reload within a tab);
//   - a SECOND tab (even in the same browser) has its own sessionStorage, so it
//     gets no stored id and is assigned a distinct seat by the REST join.
//
// The previous scheme stored a single global localStorage["tbwc_player_id"],
// which every tab shared — so a 2nd tab reused player 1's id and got evicted as
// a duplicate socket (server closes the older socket with code 4009). That is
// the bug this scoping fixes.
function playerIdKey(code: string): string {
  return `tbwc_player_id:${code.toUpperCase()}`;
}

// Fallback text for a hard-rejection close code when the server did not send an
// `error` message first. Mirrors the close codes in src/board/ws.py.
function closeCodeMessage(code: number): string {
  switch (code) {
    case 4001:
      return "Could not join this room — please return to the lobby and rejoin.";
    case 4004:
      return "Room not found.";
    case 4009:
      return "This seat was opened in another tab.";
    default:
      return "Connection rejected by the server.";
  }
}

export interface GameSocketState {
  gameState: GameStateSnapshot | null;
  log: string[];
  brewing: string | null;
  previewResult: PreviewResult | null;
  // A hard connection rejection (close code >= 4000, or a fatal socket error).
  // Retrying can never fix it, so the room page tears down to a "back to lobby"
  // screen. Cleared only when a fresh socket opens successfully.
  fatalError: string | null;
  // A recoverable, message-level error from the server ({type:'error'}), e.g.
  // "You have already drawn this turn". The game stays mounted; the UI shows a
  // dismissible banner. Auto-clears after a few seconds or on the next state
  // update, and can be dismissed manually via clearTransientError.
  transientError: string | null;
  clearTransientError: () => void;
  connected: boolean;
  // Set when the server needs the active player to pick a target for a card
  // they just played (the play is held pending server-side). The UI shows a
  // picker; answering sends a follow-up play with the choice. Cleared by
  // clearPromptChoice once handled.
  promptChoice: PromptChoiceMsg | null;
  clearPromptChoice: () => void;
  // The server-authoritative epilogue vote pool (authored cards only — never
  // blanks or shipped seed cards), broadcast once when the epilogue opens.
  // Empty until the 'epilogue' message arrives.
  epilogueCards: CardSnapshot[];
  interactionRequest: InteractionRequestMsg | null;
  interactionProgress: InteractionProgressMsg | null;
  // The last reaction window outcome ("countered!", "stolen", …), kept briefly
  // so the UI can flash it. The open window itself is NOT stored here — it is
  // driven by gameState.pending_play (the reconnect-safe source of truth).
  // Cleared automatically after a few seconds or when a new window opens.
  reactionResult: ReactionResultMsg | null;
  send: (msg: ClientMsg) => void;
}

// How long the reaction outcome flash ("Countered!") stays up.
const REACTION_RESULT_MS = 4000;

export function useGameSocket(code: string, name: string): GameSocketState {
  const wsRef = useRef<WebSocket | null>(null);
  const [gameState, setGameState] = useState<GameStateSnapshot | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [brewing, setBrewing] = useState<string | null>(null);
  const [previewResult, setPreviewResult] =
    useState<GameSocketState["previewResult"]>(null);
  const [fatalError, setFatalError] = useState<string | null>(null);
  const [transientError, setTransientError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [promptChoice, setPromptChoice] = useState<PromptChoiceMsg | null>(
    null,
  );
  const [epilogueCards, setEpilogueCards] = useState<CardSnapshot[]>([]);
  const [interactionRequest, setInteractionRequest] =
    useState<InteractionRequestMsg | null>(null);
  const [interactionProgress, setInteractionProgress] =
    useState<InteractionProgressMsg | null>(null);
  const [reactionResult, setReactionResult] =
    useState<ReactionResultMsg | null>(null);
  const reactionResultTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );

  // Pending auto-dismiss timer for the current transient error, so a newer
  // error resets the countdown instead of being cut short by an older one.
  const transientTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  // The most recent server `error` message. A hard rejection (close code >=
  // 4000) is preceded by a matching `error` payload from the server (see
  // src/board/ws.py); onclose adopts it so the fatal screen shows the server's
  // specific reason instead of the generic close-code fallback.
  const lastServerErrorRef = useRef<string | null>(null);

  const clearTransientError = useCallback(() => {
    if (transientTimeoutRef.current) {
      clearTimeout(transientTimeoutRef.current);
      transientTimeoutRef.current = null;
    }
    lastServerErrorRef.current = null;
    setTransientError(null);
  }, []);

  const send = useCallback((msg: ClientMsg) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  const clearPromptChoice = useCallback(() => setPromptChoice(null), []);

  useEffect(() => {
    if (!code || !name) return;

    let cancelled = false;
    let retryTimeout: ReturnType<typeof setTimeout>;

    function connect() {
      if (cancelled) return;
      const ws = new WebSocket(`${WS_URL}/ws/${code}`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) {
          ws.close();
          return;
        }
        setConnected(true);
        setFatalError(null);
        const storedId = sessionStorage.getItem(playerIdKey(code));
        ws.send(JSON.stringify({ type: "join", player_id: storedId, name }));
      };

      ws.onmessage = (evt) => {
        const msg: ServerMsg = JSON.parse(evt.data as string);
        switch (msg.type) {
          case "state":
            setGameState(msg.state);
            if (msg.state.pending_interaction) {
              const pending = msg.state.pending_interaction;
              setInteractionRequest((current) =>
                current?.interaction_id === pending.interaction_id
                  ? current
                  : null,
              );
              setInteractionProgress((current) => ({
                type: "interaction_progress",
                schema_version: 1,
                interaction_id: pending.interaction_id,
                deadline_at: pending.deadline_at,
                progress: {
                  ...pending.progress,
                  // Shared snapshots deliberately cannot personalize this bit.
                  // Preserve an already-known submission across reconnect until
                  // the targeted replayed request refreshes it.
                  submitted:
                    current?.interaction_id === pending.interaction_id
                      ? current.progress.submitted
                      : false,
                },
              }));
            } else {
              setInteractionRequest(null);
              setInteractionProgress(null);
            }
            // Hydrate the effect log from the authoritative state snapshot so a
            // refresh/reconnect restores full history. The backend keeps
            // state.log in sync with every effect_applied it broadcasts, so
            // replacing here is idempotent with the live appends below.
            setLog(msg.state.log ?? []);
            // A fresh authoritative state means whatever the last transient
            // error complained about has been superseded — clear it early.
            clearTransientError();
            break;
          case "effect_applied":
            setLog((prev) => [...prev, msg.log_entry]);
            setBrewing(null);
            break;
          case "brewing":
            setBrewing(msg.card_id);
            break;
          case "card_interpreted":
            setBrewing(null);
            break;
          case "preview_result":
            setPreviewResult({
              program: msg.program,
              snippet: msg.snippet,
              verdict: msg.verdict,
              mechanical_status: msg.mechanical_status,
              mechanical_reason: msg.mechanical_reason,
              correlation_id: msg.correlation_id,
            });
            break;
          case "prompt_choice":
            setBrewing(null);
            setPromptChoice(msg);
            break;
          case "interaction_request":
            setBrewing(null);
            setInteractionRequest(msg);
            setInteractionProgress({
              type: "interaction_progress",
              schema_version: 1,
              interaction_id: msg.interaction_id,
              deadline_at: msg.deadline_at,
              progress: msg.progress,
            });
            break;
          case "interaction_progress":
            setInteractionProgress(msg);
            break;
          case "reaction_window":
            // The window UI is driven by the state snapshot's pending_play
            // (broadcast right after this push); just clear any stale outcome.
            if (reactionResultTimeoutRef.current) {
              clearTimeout(reactionResultTimeoutRef.current);
              reactionResultTimeoutRef.current = null;
            }
            setReactionResult(null);
            break;
          case "reaction_result":
            setReactionResult(msg);
            if (reactionResultTimeoutRef.current) {
              clearTimeout(reactionResultTimeoutRef.current);
            }
            reactionResultTimeoutRef.current = setTimeout(() => {
              reactionResultTimeoutRef.current = null;
              setReactionResult(null);
            }, REACTION_RESULT_MS);
            break;
          case "epilogue":
            setEpilogueCards(msg.cards);
            break;
          case "error":
            // Message-level errors are recoverable gameplay/validation notices
            // (e.g. "You have already drawn this turn"). Surface them as a
            // transient banner — never tear down the game — and auto-dismiss
            // after a short delay. Reset any pending timer so a newer error
            // gets the full window.
            lastServerErrorRef.current = msg.message;
            if (transientTimeoutRef.current) {
              clearTimeout(transientTimeoutRef.current);
            }
            setTransientError(msg.message);
            transientTimeoutRef.current = setTimeout(() => {
              transientTimeoutRef.current = null;
              lastServerErrorRef.current = null;
              setTransientError(null);
            }, TRANSIENT_ERROR_MS);
            break;
          default:
            break;
        }
      };

      ws.onclose = (evt) => {
        setConnected(false);
        if (cancelled) return;
        // Application-level close codes (4xxx) are hard rejections from our
        // server that retrying can never fix: 4000 bad first message, 4001
        // unknown/null player_id, 4004 room not found, 4009 seat replaced by a
        // newer connection. Stop the reconnect loop and surface an error
        // instead of spinning forever. Transient drops (1006 abnormal close,
        // etc.) fall through and reconnect after a short delay.
        if (evt.code >= 4000) {
          // Hard rejection: surface a fatal error (drives the back-to-lobby
          // screen) and stop retrying. The server sends a specific `error`
          // payload just before these closes — prefer it, and fall back to a
          // code-specific message. Promote it out of the transient banner so
          // it isn't shown twice.
          if (transientTimeoutRef.current) {
            clearTimeout(transientTimeoutRef.current);
            transientTimeoutRef.current = null;
          }
          setTransientError(null);
          setFatalError(
            lastServerErrorRef.current ?? closeCodeMessage(evt.code),
          );
          return;
        }
        retryTimeout = setTimeout(connect, 2000);
      };

      ws.onerror = () => {
        // onerror typically precedes a transient (1006) close that reconnects.
        // We intentionally do NOT set a fatal error here: the retry loop is
        // still running, and the header already shows "Reconnecting…".
      };
    }

    connect();

    return () => {
      cancelled = true;
      clearTimeout(retryTimeout);
      if (transientTimeoutRef.current) {
        clearTimeout(transientTimeoutRef.current);
        transientTimeoutRef.current = null;
      }
      if (reactionResultTimeoutRef.current) {
        clearTimeout(reactionResultTimeoutRef.current);
        reactionResultTimeoutRef.current = null;
      }
      wsRef.current?.close();
    };
  }, [code, name, clearTransientError]);

  return {
    gameState,
    log,
    brewing,
    previewResult,
    fatalError,
    transientError,
    clearTransientError,
    connected,
    promptChoice,
    clearPromptChoice,
    epilogueCards,
    interactionRequest,
    interactionProgress,
    reactionResult,
    send,
  };
}

export function storePlayerId(code: string, playerId: string): void {
  sessionStorage.setItem(playerIdKey(code), playerId);
}

export function getPlayerId(code: string): string | null {
  return sessionStorage.getItem(playerIdKey(code));
}

export function clearPlayerId(code: string): void {
  sessionStorage.removeItem(playerIdKey(code));
}
