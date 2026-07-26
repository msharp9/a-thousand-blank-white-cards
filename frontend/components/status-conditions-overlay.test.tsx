import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StatusConditionsOverlay } from "./status-conditions-overlay";
import type { GameStateSnapshot } from "@/lib/types";

function gameState(
  overrides: Partial<GameStateSnapshot> = {},
): GameStateSnapshot {
  return {
    room_code: "ABCD",
    mode: "in_person",
    phase: "playing",
    players: [
      {
        id: "abe",
        name: "Abe",
        score: 0,
        hand: [],
        in_play: [],
        connected: true,
        conditions: { cursed: true, hidden: false },
      },
      {
        id: "brad",
        name: "Brad",
        score: 0,
        hand: [],
        in_play: [],
        connected: true,
        conditions: { poisoned: 2 },
        condition_ttls: { poisoned: 3 },
      },
    ],
    spectators: [],
    turn_index: 0,
    turn_number: 1,
    turn_order: ["abe", "brad"],
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
    cards: {
      curse: {
        id: "curse",
        title: "Curse Tax",
        description: "Old cursed-card description.",
      },
    },
    house_rules: [],
    hooks: [
      {
        id: "hook-curse-0",
        source_card_id: "curse",
        event: "on_turn_end",
        scope: "center",
        title:
          "All players who are cursed discard a card at the end of their turn.",
        condition_keys: ["cursed"],
      },
    ],
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

describe("StatusConditionsOverlay", () => {
  it("shows human-readable conditions, durations, values, and hook tags", () => {
    render(
      <StatusConditionsOverlay gameState={gameState()} onClose={() => {}} />,
    );
    expect(screen.getByText("Abe is Cursed")).toBeTruthy();
    expect(screen.getByText("Brad is Poisoned for 3 more turns")).toBeTruthy();
    expect(screen.getByText("2 stacks")).toBeTruthy();
    expect(screen.queryByText(/hidden/i)).toBeNull();
    expect(
      screen.getByText(
        "All players who are cursed discard a card at the end of their turn.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Affects: Cursed")).toBeTruthy();
    expect(screen.getByText("Affects everyone")).toBeTruthy();
    expect(screen.getByText("From Curse Tax")).toBeTruthy();
  });

  it("uses a human-readable fallback without exposing hook internals", () => {
    const state = gameState({
      hooks: [
        {
          id: "hook-missing-0",
          source_card_id: "missing",
          event: "on_turn_start",
          scope: "center",
        },
      ],
    });
    render(<StatusConditionsOverlay gameState={state} onClose={() => {}} />);
    expect(
      screen.getByText("A standing rule applies at the start of each turn."),
    ).toBeTruthy();
    expect(screen.queryByText("hook-missing-0")).toBeNull();
    expect(screen.queryByText("on_turn_start")).toBeNull();
  });

  it("shows friendly empty states and closes from the shell", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const state = gameState({
      players: gameState().players.map((player) => ({
        ...player,
        conditions: {},
      })),
      hooks: [],
    });
    render(<StatusConditionsOverlay gameState={state} onClose={onClose} />);
    expect(screen.getByText("No active status conditions.")).toBeTruthy();
    expect(screen.getByText("No active hooks.")).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "Close status conditions" }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
