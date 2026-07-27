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

  it("queues live arbiter comments without replaying hydrated log entries", () => {
    const { result } = renderHook(() => useGameSocket("ABCD", "Alice"));
    const ws = MockWebSocket.instances[0];

    act(() => ws.onopen?.());
    act(() =>
      ws.emit({
        type: "state",
        state: baseState({ log: ["🤖 An old reconnect comment"] }),
      }),
    );
    expect(result.current.arbiterNotices).toEqual([]);

    act(() =>
      ws.emit({
        type: "effect_applied",
        log_entry: "🤖 A fresh live comment",
      }),
    );
    expect(result.current.log).toContain("🤖 A fresh live comment");
    expect(result.current.arbiterNotices[0]).toMatchObject({
      kind: "arbiter",
      message: "A fresh live comment",
    });

    act(() =>
      ws.emit({
        type: "effect_applied",
        log_entry: "Alice gains 2 points",
      }),
    );
    expect(result.current.arbiterNotices).toHaveLength(1);
  });

  it("preserves every dice result in FIFO order", () => {
    const { result } = renderHook(() => useGameSocket("ABCD", "Alice"));
    const ws = MockWebSocket.instances[0];
    const roll = {
      type: "dice_roll",
      actor_id: "p1",
      sides: 6,
      values: [3],
      total: 3,
      card_id: "c1",
    };

    act(() => ws.onopen?.());
    act(() => {
      ws.emit(roll);
      ws.emit({ ...roll, values: [5], total: 5 });
    });

    expect(result.current.topNotices).toHaveLength(2);
    expect(result.current.topNotices.map((notice) => notice.kind)).toEqual([
      "dice",
      "dice",
    ]);
    const firstId = result.current.topNotices[0].id;
    act(() => result.current.dismissNotice(firstId));
    expect(result.current.topNotices).toHaveLength(1);
    expect(result.current.topNotices[0]).toMatchObject({
      kind: "dice",
      roll: { total: 5 },
    });
  });

  it("keeps privileged admin state separate and clears it on phase exit", () => {
    const { result } = renderHook(() => useGameSocket("ABCD", "Morgan"));
    const ws = MockWebSocket.instances[0];
    const privileged = baseState({
      host_id: "spectator-host",
      deck: ["secret-card"],
    });

    act(() => ws.onopen?.());
    act(() => ws.emit({ type: "admin_state", state: privileged }));
    expect(result.current.adminGameState?.deck).toEqual(["secret-card"]);
    expect(result.current.gameState).toBeNull();

    act(() =>
      ws.emit({ type: "state", state: baseState({ phase: "results" }) }),
    );
    expect(result.current.adminGameState).toBeNull();
  });
});
