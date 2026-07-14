import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GameStateSnapshot } from "@/lib/types";
import { DynamicStatePanel } from "./dynamic-state-panel";

const { isDevMode } = vi.hoisted(() => ({ isDevMode: vi.fn(() => true) }));
vi.mock("@/lib/dev", () => ({ isDevMode }));

function makeGameState(
  overrides: Partial<GameStateSnapshot> = {},
): GameStateSnapshot {
  return {
    room_code: "ABCD",
    phase: "playing",
    players: [
      {
        id: "p1",
        name: "Alice",
        score: 3,
        hand: [],
        in_play: [],
        connected: true,
        conditions: {},
      },
      {
        id: "p2",
        name: "Bob",
        score: 1,
        hand: [],
        in_play: [],
        connected: true,
        conditions: { frozen: true },
      },
    ],
    spectators: [],
    turn_index: 1,
    turn_number: 2,
    turn_order: ["p1", "p2"],
    rules: {
      draw: 1,
      play: 1,
      cannot_play: {},
      end_condition: { type: "deck_empty" },
      win_condition: { kind: "score" },
      extra: {},
    },
    draw_count: 0,
    deck: [],
    discard: [],
    cards: {},
    history_events: [],
    house_rules: [],
    hooks: [],
    has_drawn: true,
    can_pass: true,
    setup_progress: {},
    cards_to_author: 5,
    winner_ids: [],
    epilogue_result: null,
    log: [],
    ...overrides,
  };
}

describe("DynamicStatePanel", () => {
  beforeEach(() => {
    isDevMode.mockReturnValue(true);
  });

  it("renders nothing when not in dev mode", () => {
    isDevMode.mockReturnValue(false);
    const { container } = render(
      <DynamicStatePanel gameState={makeGameState()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders sections from a fake game state", () => {
    render(<DynamicStatePanel gameState={makeGameState()} />);
    expect(screen.getByText("Dynamic game state")).toBeInTheDocument();
    expect(screen.getByText("Turn order")).toBeInTheDocument();
    expect(screen.getByText("Rules")).toBeInTheDocument();
    expect(screen.getByText("Conditions & hooks")).toBeInTheDocument();
    expect(screen.getByText("Bob: frozen=true")).toBeInTheDocument();
  });

  it("colors turn-order chips with each player's shared identity color", () => {
    render(<DynamicStatePanel gameState={makeGameState()} />);
    const alice = screen.getByText("Alice");
    const bob = screen.getByText("Bob");
    expect(alice.style.color).toBe("var(--player-0)");
    expect(bob.style.color).toBe("var(--player-1)");
  });

  it("marks the active player with a data flag, not a different background", () => {
    render(<DynamicStatePanel gameState={makeGameState({ turn_index: 1 })} />);
    const active = screen.getByText("Bob");
    const inactive = screen.getByText("Alice");
    expect(active).toHaveAttribute("data-active", "true");
    expect(inactive).not.toHaveAttribute("data-active");
    expect(active.className).not.toMatch(/bg-/);
    expect(inactive.className).not.toMatch(/bg-/);
  });
});
