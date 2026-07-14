import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HistoryModal } from "./history-modal";
import type {
  CardSnapshot,
  GameStateSnapshot,
  HistoryEventSnapshot,
} from "@/lib/types";

function baseState(
  overrides: Partial<GameStateSnapshot> = {},
): GameStateSnapshot {
  return {
    room_code: "ABCD",
    phase: "playing",
    players: [
      {
        id: "p1",
        name: "Alice",
        score: 5,
        hand: [],
        in_play: [],
        connected: true,
        conditions: {},
      },
      {
        id: "p2",
        name: "Bob",
        score: 2,
        hand: [],
        in_play: [],
        connected: true,
        conditions: {},
      },
    ],
    spectators: [],
    turn_index: 0,
    turn_number: 1,
    turn_order: ["p1", "p2"],
    rules: {
      draw: 1,
      play: 1,
      cannot_play: {},
      end_condition: { type: "deck_empty" },
      win_condition: { kind: "highest_points" },
      extra: {},
    },
    draw_count: 1,
    deck: [],
    discard: [],
    cards: {},
    house_rules: [],
    hooks: [],
    has_drawn: true,
    can_pass: false,
    setup_progress: {},
    cards_to_author: 5,
    winner_ids: [],
    epilogue_result: null,
    history_events: [],
    log: [],
    ...overrides,
  };
}

const zap: CardSnapshot = {
  id: "zap",
  title: "Zap",
  description: "Skip the next player.",
};
const bless: CardSnapshot = {
  id: "bless",
  title: "Bless",
  description: "Give points to a friend.",
};

function playEvent(
  overrides: Partial<HistoryEventSnapshot> = {},
): HistoryEventSnapshot {
  return {
    sequence: 1,
    kind: "play",
    actor_id: "p1",
    target_player_ids: [],
    card_id: "zap",
    ...overrides,
  };
}

describe("HistoryModal", () => {
  it("shows an empty state when nothing has been played", () => {
    render(
      <HistoryModal
        open
        onOpenChange={() => {}}
        gameState={baseState()}
        roomCode="ABCD"
      />,
    );
    expect(screen.getByText("Everything Played")).toBeTruthy();
    expect(screen.getByText("No cards played yet.")).toBeTruthy();
  });

  it("renders plays newest-first with by/target/turn metadata", () => {
    const state = baseState({
      cards: { zap: zap, bless: bless },
      history_events: [
        playEvent({
          sequence: 1,
          card_id: "zap",
          actor_id: "p1",
          turn: 1,
          target_player_ids: [],
        }),
        playEvent({
          sequence: 2,
          card_id: "bless",
          actor_id: "p2",
          turn: 2,
          target_player_ids: ["p1"],
        }),
      ],
    });
    render(
      <HistoryModal
        open
        onOpenChange={() => {}}
        gameState={state}
        roomCode="ABCD"
      />,
    );
    const rows = screen.getAllByRole("listitem");
    // Newest first: Bless (sequence 2) renders before Zap (sequence 1).
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Bob")).toBeTruthy();
    expect(within(rows[0]).getByText("Turn 2")).toBeTruthy();
    expect(within(rows[1]).getByText(/Alice/)).toBeTruthy();
    expect(within(rows[1]).getByText("Turn 1")).toBeTruthy();
  });

  it("omits target metadata when the event carries none", () => {
    const state = baseState({
      cards: { zap },
      history_events: [playEvent({ target_player_ids: [] })],
    });
    render(
      <HistoryModal
        open
        onOpenChange={() => {}}
        gameState={state}
        roomCode="ABCD"
      />,
    );
    expect(screen.getByText("Alice")).toBeTruthy();
    expect(screen.queryByText(/→/)).toBeNull();
  });

  it("labels a target covering every player as Everyone", () => {
    const state = baseState({
      cards: { zap },
      history_events: [
        playEvent({ actor_id: "p1", target_player_ids: ["p1", "p2"] }),
      ],
    });
    render(
      <HistoryModal
        open
        onOpenChange={() => {}}
        gameState={state}
        roomCode="ABCD"
      />,
    );
    expect(screen.getByText(/Everyone/)).toBeTruthy();
  });

  it("joins names for a multi-player target that is a strict subset", () => {
    const state = baseState({
      players: [
        ...baseState().players,
        {
          id: "p3",
          name: "Cara",
          score: 0,
          hand: [],
          in_play: [],
          connected: true,
          conditions: {},
        },
      ],
      cards: { zap },
      history_events: [
        playEvent({ actor_id: "p3", target_player_ids: ["p1", "p2"] }),
      ],
    });
    render(
      <HistoryModal
        open
        onOpenChange={() => {}}
        gameState={state}
        roomCode="ABCD"
      />,
    );
    expect(screen.getByText(/Alice, Bob/)).toBeTruthy();
    expect(screen.queryByText(/Everyone/)).toBeNull();
  });

  it("ignores non-play history events", () => {
    const state = baseState({
      cards: { zap },
      history_events: [
        {
          sequence: 1,
          kind: "draw",
          actor_id: "p1",
          target_player_ids: ["p1"],
          amount: 1,
        },
        playEvent({ sequence: 2 }),
      ],
    });
    render(
      <HistoryModal
        open
        onOpenChange={() => {}}
        gameState={state}
        roomCode="ABCD"
      />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("closes via the X button", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <HistoryModal
        open
        onOpenChange={onOpenChange}
        gameState={baseState()}
        roomCode="ABCD"
      />,
    );
    await user.click(screen.getByRole("button", { name: /close/i }));
    expect(onOpenChange.mock.calls[0]?.[0]).toBe(false);
  });
});
