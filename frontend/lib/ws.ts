"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  CardSnapshot,
  ClientMsg,
  GameStateSnapshot,
  HandRevealedMsg,
  InteractionProgressMsg,
  InteractionRequestMsg,
  PreviewResult,
  PromptChoiceMsg,
  ServerMsg,
  TurnTimerSnapshot,
} from "./types";
import {
  dismissNotice as removeNotice,
  enqueueNotice,
  parseArbiterLogEntry,
  type ViewportNotice,
} from "./notices";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

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
  // Live-only transient events. Each lane is FIFO; the viewport notice host
  // renders and times only its first item, so bursts are never overwritten.
  topNotices: ViewportNotice[];
  arbiterNotices: ViewportNotice[];
  dismissNotice: (id: string) => void;
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
  // A one-shot hand reveal targeted at THIS client (hand_revealed push).
  // Deliberately transient — not part of the state snapshot, lost on
  // reconnect — and dismissed via clearHandReveal.
  handReveal: HandRevealedMsg | null;
  clearHandReveal: () => void;
  // The live pausable turn clock (rules.turn_timer), or null when no clock is
  // armed. Re-synced from every turn_timer push AND every state snapshot, so
  // it survives reconnects like the reaction window's deadline.
  turnTimer: TurnTimerSnapshot | null;
  send: (msg: ClientMsg) => void;
}

export function useGameSocket(code: string, name: string): GameSocketState {
  const wsRef = useRef<WebSocket | null>(null);
  const [gameState, setGameState] = useState<GameStateSnapshot | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [brewing, setBrewing] = useState<string | null>(null);
  const [previewResult, setPreviewResult] =
    useState<GameSocketState["previewResult"]>(null);
  const [fatalError, setFatalError] = useState<string | null>(null);
  const [topNotices, setTopNotices] = useState<ViewportNotice[]>([]);
  const [arbiterNotices, setArbiterNotices] = useState<ViewportNotice[]>([]);
  const [connected, setConnected] = useState(false);
  const [promptChoice, setPromptChoice] = useState<PromptChoiceMsg | null>(
    null,
  );
  const [epilogueCards, setEpilogueCards] = useState<CardSnapshot[]>([]);
  const [interactionRequest, setInteractionRequest] =
    useState<InteractionRequestMsg | null>(null);
  const [interactionProgress, setInteractionProgress] =
    useState<InteractionProgressMsg | null>(null);
  const [handReveal, setHandReveal] = useState<HandRevealedMsg | null>(null);
  const [turnTimer, setTurnTimer] = useState<TurnTimerSnapshot | null>(null);
  const noticeSequenceRef = useRef(0);
  // The most recent server `error` message. A hard rejection (close code >=
  // 4000) is preceded by a matching `error` payload from the server (see
  // src/board/ws.py); onclose adopts it so the fatal screen shows the server's
  // specific reason instead of the generic close-code fallback.
  const lastServerErrorRef = useRef<string | null>(null);

  const dismissNotice = useCallback((id: string) => {
    setTopNotices((queue) => {
      const next = removeNotice(queue, id);
      if (
        lastServerErrorRef.current &&
        !next.some(
          (notice) =>
            notice.kind === "error" &&
            notice.message === lastServerErrorRef.current,
        )
      ) {
        lastServerErrorRef.current = null;
      }
      return next;
    });
    setArbiterNotices((queue) => removeNotice(queue, id));
  }, []);

  const nextNoticeId = useCallback(
    (kind: ViewportNotice["kind"]) =>
      `${kind}-${Date.now()}-${noticeSequenceRef.current++}`,
    [],
  );

  const send = useCallback((msg: ClientMsg) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  const clearPromptChoice = useCallback(() => setPromptChoice(null), []);
  const clearHandReveal = useCallback(() => setHandReveal(null), []);

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
            // Authoritative re-sync of the turn clock (covers reconnects that
            // missed the live turn_timer pushes).
            setTurnTimer(msg.state.turn_timer ?? null);
            // Hydrate the effect log from the authoritative state snapshot so a
            // refresh/reconnect restores full history. The backend keeps
            // state.log in sync with every effect_applied it broadcasts, so
            // replacing here is idempotent with the live appends below.
            setLog(msg.state.log ?? []);
            // brewing is a transient set by the one-shot "brewing" push and
            // cleared by its card_interpreted/effect_applied follow-up. An
            // authoritative state means any in-flight interpretation for this
            // client is over (the room lock serialises a play's whole
            // interpretation, so no state snapshot interleaves the pair in
            // normal flow) — clearing here rescues a reconnect that missed the
            // clearing push, which would otherwise soft-lock the hand.
            setBrewing(null);
            break;
          case "effect_applied":
            setLog((prev) => [...prev, msg.log_entry]);
            setBrewing(null);
            {
              const comment = parseArbiterLogEntry(msg.log_entry);
              if (comment) {
                setArbiterNotices((queue) =>
                  enqueueNotice(queue, {
                    id: nextNoticeId("arbiter"),
                    lane: "arbiter",
                    kind: "arbiter",
                    message: comment,
                    timeoutMs: 7000,
                  }),
                );
              }
            }
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
          case "hand_revealed":
            setHandReveal(msg);
            break;
          case "turn_timer":
            // A cleared clock pushes all-null fields; player_id is set for
            // every live (running or paused) clock.
            setTurnTimer(
              msg.player_id
                ? {
                    deadline_epoch_ms: msg.deadline_epoch_ms,
                    paused: msg.paused,
                    player_id: msg.player_id,
                  }
                : null,
            );
            break;
          case "reaction_window":
            // The window UI is driven by the state snapshot's pending_play.
            break;
          case "reaction_result":
            if (msg.outcome !== "resolved") {
              setTopNotices((queue) =>
                enqueueNotice(queue, {
                  id: nextNoticeId("reaction"),
                  lane: "top",
                  kind: "reaction",
                  result: msg,
                  timeoutMs: 4000,
                }),
              );
            }
            break;
          case "dice_roll":
            setTopNotices((queue) =>
              enqueueNotice(queue, {
                id: nextNoticeId("dice"),
                lane: "top",
                kind: "dice",
                roll: msg,
                timeoutMs: 5000,
              }),
            );
            break;
          case "epilogue":
            setEpilogueCards(msg.cards);
            break;
          case "admin_proposal_result":
            setTopNotices((queue) =>
              enqueueNotice(queue, {
                id: nextNoticeId("admin"),
                lane: "top",
                kind: "admin",
                message: msg.message,
                outcome: msg.outcome,
                timeoutMs: 5000,
              }),
            );
            break;
          case "error":
            lastServerErrorRef.current = msg.message;
            setTopNotices((queue) =>
              enqueueNotice(queue, {
                id: nextNoticeId("error"),
                lane: "top",
                kind: "error",
                message: msg.message,
                timeoutMs: 4500,
              }),
            );
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
          setTopNotices((queue) =>
            queue.filter((notice) => notice.kind !== "error"),
          );
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
      wsRef.current?.close();
    };
  }, [code, name, nextNoticeId]);

  return {
    gameState,
    log,
    brewing,
    previewResult,
    fatalError,
    topNotices,
    arbiterNotices,
    dismissNotice,
    connected,
    promptChoice,
    clearPromptChoice,
    epilogueCards,
    interactionRequest,
    interactionProgress,
    handReveal,
    clearHandReveal,
    turnTimer,
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
