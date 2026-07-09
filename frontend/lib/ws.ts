"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ClientMsg, GameStateSnapshot, ServerMsg } from "./types";

const PLAYER_ID_KEY = "tbwc_player_id";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

export interface GameSocketState {
  gameState: GameStateSnapshot | null;
  log: string[];
  brewing: string | null;
  previewResult: { program?: string | null; snippet?: string | null; verdict: string } | null;
  error: string | null;
  connected: boolean;
  send: (msg: ClientMsg) => void;
}

export function useGameSocket(code: string, name: string): GameSocketState {
  const wsRef = useRef<WebSocket | null>(null);
  const [gameState, setGameState] = useState<GameStateSnapshot | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [brewing, setBrewing] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<GameSocketState["previewResult"]>(null);
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
        const storedId = localStorage.getItem(PLAYER_ID_KEY);
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
            setPreviewResult({ program: msg.program, snippet: msg.snippet, verdict: msg.verdict });
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

export function storePlayerId(playerId: string): void {
  localStorage.setItem(PLAYER_ID_KEY, playerId);
}

export function clearPlayerId(): void {
  localStorage.removeItem(PLAYER_ID_KEY);
}
