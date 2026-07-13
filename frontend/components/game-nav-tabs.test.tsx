import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { GameNavTabs } from "./game-nav-tabs";
import type { GameStateSnapshot } from "@/lib/types";

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
    ],
    spectators: [],
    turn_index: 0,
    turn_number: 1,
    turn_order: ["p1"],
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
    cards: { zap: { id: "zap", title: "Zap", description: "Skip." } },
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

describe("GameNavTabs", () => {
  it("defaults to Table with no overlay mounted", () => {
    render(<GameNavTabs gameState={baseState()} roomCode="ABCD" />);
    expect(screen.getByRole("button", { name: "Table" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.queryByText("The Deck")).toBeNull();
    expect(screen.queryByText("Scoreboard")).toBeNull();
  });

  it("mounts the Gallery overlay on tab click and unmounts on Table", async () => {
    const user = userEvent.setup();
    render(<GameNavTabs gameState={baseState()} roomCode="ABCD" />);

    await user.click(screen.getByRole("button", { name: "Gallery" }));
    expect(screen.getByText("The Deck")).toBeTruthy();
    expect(screen.getByText("Zap")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.queryByText("The Deck")).toBeNull();
  });

  it("mounts the Scores overlay on tab click and unmounts on Table", async () => {
    const user = userEvent.setup();
    render(<GameNavTabs gameState={baseState()} roomCode="ABCD" />);

    await user.click(screen.getByRole("button", { name: "Scores" }));
    expect(screen.getByText("Scoreboard")).toBeTruthy();
    expect(screen.getByText("Alice 🥇")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.queryByText("Scoreboard")).toBeNull();
  });

  it("switches directly between overlays without needing Table in between", async () => {
    const user = userEvent.setup();
    render(<GameNavTabs gameState={baseState()} roomCode="ABCD" />);

    await user.click(screen.getByRole("button", { name: "Gallery" }));
    expect(screen.getByText("The Deck")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Scores" }));
    expect(screen.queryByText("The Deck")).toBeNull();
    expect(screen.getByText("Scoreboard")).toBeTruthy();
  });

  it("closes the open overlay on Escape", async () => {
    const user = userEvent.setup();
    render(<GameNavTabs gameState={baseState()} roomCode="ABCD" />);

    await user.click(screen.getByRole("button", { name: "Gallery" }));
    expect(screen.getByText("The Deck")).toBeTruthy();

    await user.keyboard("{Escape}");
    expect(screen.queryByText("The Deck")).toBeNull();
    expect(screen.getByRole("button", { name: "Table" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
