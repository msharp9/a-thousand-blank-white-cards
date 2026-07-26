import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { conditionLabel, GameTable } from "./game-table";
import type { GameStateSnapshot, PlayerSnapshot } from "@/lib/types";

function player(overrides: Partial<PlayerSnapshot> = {}): PlayerSnapshot {
  return {
    id: "p1",
    name: "Alice",
    score: 0,
    hand: [],
    in_play: [],
    connected: true,
    conditions: {},
    ...overrides,
  };
}

function baseState(
  overrides: Partial<GameStateSnapshot> = {},
): GameStateSnapshot {
  return {
    room_code: "ABCD",
    mode: "online",
    phase: "playing",
    players: [player(), player({ id: "me", name: "Me" })],
    spectators: [],
    turn_index: 0,
    turn_number: 1,
    turn_order: ["p1", "me"],
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

describe("conditionLabel", () => {
  it("gives reserved keys friendly labels", () => {
    expect(conditionLabel("skip_next", true)).toBe("skips next turn");
    expect(conditionLabel("extra_turn", true)).toBe("extra turn");
  });

  it("humanizes free-form keys and appends numeric stacks", () => {
    expect(conditionLabel("on_fire", true)).toBe("on fire");
    expect(conditionLabel("poisoned", 3)).toBe("poisoned ×3");
  });

  it("appends the TTL, singular and plural, combined with stacks", () => {
    expect(conditionLabel("poisoned", true, 1)).toBe("poisoned, 1 turn left");
    expect(conditionLabel("poisoned", 3, 2)).toBe("poisoned ×3, 2 turns left");
  });

  it("renders a TTL of 0 as the condition's last active turn", () => {
    expect(conditionLabel("poisoned", true, 0)).toBe("poisoned, last turn");
  });
});

describe("GameTable condition badges", () => {
  it("shows badges under a conditioned opponent's seat", () => {
    const state = baseState();
    state.players[0].conditions = { skip_next: true, poisoned: 2 };
    state.players[0].condition_ttls = { poisoned: 2 };
    render(<GameTable gameState={state} myPlayerId="me" />);
    expect(screen.getByText("skips next turn")).toBeTruthy();
    expect(screen.getByText("poisoned ×2, 2 turns left")).toBeTruthy();
  });

  it("renders no badge for falsy-valued (toggled-off) conditions", () => {
    const state = baseState();
    state.players[0].conditions = { frozen: false };
    render(<GameTable gameState={state} myPlayerId="me" />);
    expect(screen.queryByText(/frozen/)).toBeNull();
  });
});
