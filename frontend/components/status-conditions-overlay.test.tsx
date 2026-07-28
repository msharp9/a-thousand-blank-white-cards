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
      cannot_play: { draw: 1 },
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
  it("shows the Rules & Status framing with Core Rules before Players", () => {
    render(
      <StatusConditionsOverlay gameState={gameState()} onClose={() => {}} />,
    );
    expect(screen.getByText("Rules & Status")).toBeTruthy();
    expect(
      screen.getByText("The rules and effects shaping this game."),
    ).toBeTruthy();
    const headings = screen
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(headings).toEqual(["Core Rules", "Players", "Reactionary Rules"]);
    expect(screen.queryByText("Active Hooks")).toBeNull();
    expect(screen.queryByText("Status Conditions")).toBeNull();
  });

  it("renders the default core rules in plain language", () => {
    render(
      <StatusConditionsOverlay gameState={gameState()} onClose={() => {}} />,
    );
    expect(
      screen.getByText("Draw 1 card at the start of your turn."),
    ).toBeTruthy();
    expect(screen.getByText("Play 1 card on your turn.")).toBeTruthy();
    expect(screen.getByText("If you cannot play, draw 1 card.")).toBeTruthy();
    expect(
      screen.getByText(
        "Game ends when the deck runs out, after the current turn.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Winner: highest score.")).toBeTruthy();
  });

  it("renders mutated rules with thresholds and modifiers readably", () => {
    const state = gameState({
      rules: {
        draw: 2,
        play: 0,
        cannot_play: {},
        end_condition: { type: "points_reached", threshold: 20 },
        win_condition: { kind: "first_to", threshold: 20 },
        hand_limit: 5,
        turn_timer: 30,
        skip_predicate: "skip_if_cursed",
        extra: { gravity_reversed: true },
      },
    });
    render(<StatusConditionsOverlay gameState={state} onClose={() => {}} />);
    expect(
      screen.getByText("Draw 2 cards at the start of your turn."),
    ).toBeTruthy();
    expect(
      screen.getByText("No cards may be played on your turn."),
    ).toBeTruthy();
    expect(
      screen.getByText("Game ends when a player reaches 20 points."),
    ).toBeTruthy();
    expect(screen.getByText("Winner: first to 20 points.")).toBeTruthy();
    expect(screen.getByText("Hand limit: 5 cards.")).toBeTruthy();
    expect(
      screen.getByText("Each turn has a 30-second time limit."),
    ).toBeTruthy();
    expect(
      screen.getByText("Some turns may be skipped (Skip If Cursed)."),
    ).toBeTruthy();
    expect(screen.getByText("Gravity Reversed is in effect.")).toBeTruthy();
    expect(
      screen.queryByText(/points_reached|first_to|skip_if_cursed/),
    ).toBeNull();
  });

  it("renders core rules in the sidebar presentation too", () => {
    render(
      <StatusConditionsOverlay
        gameState={gameState()}
        presentation="sidebar"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Core Rules")).toBeTruthy();
    expect(
      screen.getByText("Draw 1 card at the start of your turn."),
    ).toBeTruthy();
    expect(screen.getByText("Reactionary Rules")).toBeTruthy();
  });

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
    expect(screen.getByText("No reactionary rules are active.")).toBeTruthy();
    expect(screen.queryByText("No active hooks.")).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "Close rules and status" }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
