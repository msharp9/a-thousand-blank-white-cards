import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGameSocket } from "./ws";

// Minimal controllable WebSocket stand-in: tests drive onopen/onmessage by hand.
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  readyState = MockWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((evt: { data: string }) => void) | null = null;
  onclose: ((evt: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }

  emit(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

function baseState(overrides: Record<string, unknown> = {}) {
  return {
    room_code: "ABCD",
    phase: "playing",
    players: [],
    spectators: [],
    deck: [],
    discard: [],
    cards: {},
    history_events: [],
    log: [],
    turn_index: 0,
    turn_order: [],
    turn_number: 1,
    rules: {},
    ...overrides,
  };
}

describe("useGameSocket brewing lifecycle", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
    vi.stubGlobal("sessionStorage", {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("clears a stale brewing flag when an authoritative state arrives", () => {
    const { result } = renderHook(() => useGameSocket("ABCD", "Alice"));
    const ws = MockWebSocket.instances[0];

    act(() => ws.onopen?.());
    act(() => ws.emit({ type: "brewing", card_id: "c1" }));
    expect(result.current.brewing).toBe("c1");

    // A reconnect/refresh replays the full state without the one-shot
    // card_interpreted push that normally clears brewing; the state handler
    // must clear it or the hand stays soft-locked forever.
    act(() => ws.emit({ type: "state", state: baseState() }));
    expect(result.current.brewing).toBeNull();
  });

  it("still clears brewing via the normal card_interpreted push", () => {
    const { result } = renderHook(() => useGameSocket("ABCD", "Alice"));
    const ws = MockWebSocket.instances[0];

    act(() => ws.onopen?.());
    act(() => ws.emit({ type: "brewing", card_id: "c1" }));
    act(() => ws.emit({ type: "card_interpreted", card_id: "c1" }));
    expect(result.current.brewing).toBeNull();
  });
});
