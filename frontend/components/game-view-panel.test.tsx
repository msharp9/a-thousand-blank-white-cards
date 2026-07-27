import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GameViewPanel } from "./game-view-panel";
import type { GameStateSnapshot } from "@/lib/types";

function state(): GameStateSnapshot {
  return {
    room_code: "ABCD",
    mode: "online",
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
  };
}

describe("GameViewPanel", () => {
  it("renders Play Log as a modal below the wide breakpoint", () => {
    render(
      <GameViewPanel
        view="log"
        gameState={state()}
        roomCode="ABCD"
        log={["Alice played Zap"]}
        brewing={null}
        presentation="modal"
        send={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: "Play Log" })).toBeTruthy();
    expect(screen.getByText("Alice played Zap")).toBeTruthy();
  });

  it("renders the selected view as a non-modal sidebar", () => {
    render(
      <GameViewPanel
        view="scores"
        gameState={state()}
        roomCode="ABCD"
        log={[]}
        brewing={null}
        presentation="sidebar"
        send={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("complementary", { name: "Scoreboard" }),
    ).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
