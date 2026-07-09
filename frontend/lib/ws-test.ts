/**
 * ws-test — standalone diagnostic to verify a cross-origin wss connection.
 *
 * Not part of the game UI. Import and call from a browser console or a temporary
 * page to confirm the deployed frontend can reach the backend's wss:// endpoint
 * (a common failure mode when CORS/origin or the wss URL is misconfigured).
 *
 *   import { testWsConnection } from "@/lib/ws-test";
 *   testWsConnection("ABCDEF").then((r) => console.log(r));
 */

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

export interface WsTestResult {
  ok: boolean;
  url: string;
  openedMs: number | null;
  firstMessage: unknown | null;
  error: string | null;
}

/**
 * Attempt to open a WebSocket to the backend and receive one message.
 * Resolves within `timeoutMs` regardless of outcome.
 */
export function testWsConnection(
  code: string,
  timeoutMs = 8000,
): Promise<WsTestResult> {
  const url = `${WS_URL}/ws/${code}`;
  const start = Date.now();

  return new Promise<WsTestResult>((resolve) => {
    let settled = false;
    const result: WsTestResult = {
      ok: false,
      url,
      openedMs: null,
      firstMessage: null,
      error: null,
    };

    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      resolve({ ...result, error: e instanceof Error ? e.message : String(e) });
      return;
    }

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      result.error = result.error ?? `timeout after ${timeoutMs}ms`;
      try {
        ws.close();
      } catch {
        // ignore
      }
      resolve(result);
    }, timeoutMs);

    ws.onopen = () => {
      result.openedMs = Date.now() - start;
      // Send a join with a null player_id (diagnostic only; server will reject,
      // but a reply proves the handshake + message round-trip works).
      ws.send(
        JSON.stringify({ type: "join", player_id: null, name: "ws-test" }),
      );
    };

    ws.onmessage = (evt) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        result.firstMessage = JSON.parse(evt.data as string);
      } catch {
        result.firstMessage = evt.data;
      }
      result.ok = result.openedMs !== null;
      ws.close();
      resolve(result);
    };

    ws.onerror = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      result.error =
        "WebSocket error (check wss URL, CORS, and backend availability)";
      resolve(result);
    };
  });
}
