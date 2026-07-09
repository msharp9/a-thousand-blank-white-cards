"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ClientMsg, GameStateSnapshot, ServerMsg } from "./types";

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

export interface GameSocketState {
  gameState: GameStateSnapshot | null;
  log: string[];
  brewing: string | null;
  previewResult: {
    program?: string | null;
    snippet?: string | null;
    verdict: string;
  } | null;
  error: string | null;
  connected: boolean;
  send: (msg: ClientMsg) => void;
}

export function useGameSocket(code: string, name: string): GameSocketState {
  const wsRef = useRef<WebSocket | null>(null);
  const [gameState, setGameState] = useState<GameStateSnapshot | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [brewing, setBrewing] = useState<string | null>(null);
  const [previewResult, setPreviewResult] =
    useState<GameSocketState["previewResult"]>(null);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  const send = useCallback((msg: ClientMsg) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

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
        setError(null);
        const storedId = sessionStorage.getItem(playerIdKey(code));
        ws.send(JSON.stringify({ type: "join", player_id: storedId, name }));
      };

      ws.onmessage = (evt) => {
        const msg: ServerMsg = JSON.parse(evt.data as string);
        switch (msg.type) {
          case "state":
            setGameState(msg.state);
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
            });
            break;
          case "error":
            setError(msg.message);
            break;
          default:
            break;
        }
      };

      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) {
          retryTimeout = setTimeout(connect, 2000);
        }
      };

      ws.onerror = () => {
        setError("WebSocket error — reconnecting…");
      };
    }

    connect();

    return () => {
      cancelled = true;
      clearTimeout(retryTimeout);
      wsRef.current?.close();
    };
  }, [code, name]);

  return { gameState, log, brewing, previewResult, error, connected, send };
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
