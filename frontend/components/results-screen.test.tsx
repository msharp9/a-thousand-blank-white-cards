import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { EpilogueResultSummary, GameStateSnapshot } from "@/lib/types";
import { ResultsScreen } from "./results-screen";

function makeGameState(
  overrides: Partial<GameStateSnapshot> = {},
): GameStateSnapshot {
  return {
    room_code: "ABCD",
    mode: "online",
    phase: "ended",
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
        conditions: {},
      },
    ],
    spectators: [],
    turn_index: 0,
    turn_number: 9,
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
    winner_ids: ["p1"],
    epilogue_result: null,
    log: [],
    ...overrides,
  };
}

function renderResults(epilogueResult: EpilogueResultSummary) {
  return render(
    <ResultsScreen
      gameState={makeGameState({ epilogue_result: epilogueResult })}
      myPlayerId="p1"
      log={[]}
      isHost={false}
      send={vi.fn()}
      onBack={vi.fn()}
    />,
  );
}

describe("ResultsScreen epilogue favorites", () => {
  it("highlights a single favorite in the Kept column with accessible text", () => {
    renderResults({
      kept: [
        { id: "k1", title: "Chaos Goose" },
        { id: "k2", title: "Quiet Card" },
      ],
      destroyed: [{ id: "d1", title: "Dud" }],
      favorite_card_ids: ["k1"],
    });

    expect(screen.getAllByText("Table favorite").length).toBe(1);
    expect(
      screen.getAllByText("Most Keep votes this game", { exact: false }).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("Chaos Goose").length).toBeGreaterThan(0);
    expect(screen.queryByText(/unanimous/i)).toBeNull();
  });

  it("lists every tied favorite in the summary and badges each kept card", () => {
    renderResults({
      kept: [
        { id: "k1", title: "Chaos Goose" },
        { id: "k2", title: "Point Fountain" },
      ],
      destroyed: [],
      favorite_card_ids: ["k1", "k2"],
    });

    expect(screen.getByText(/Table favorites/)).toBeTruthy();
    expect(
      screen.getByText("Chaos Goose, Point Fountain", { exact: false }),
    ).toBeTruthy();
    expect(screen.getAllByText("Table favorite").length).toBe(2);
    expect(screen.queryByText(/unanimous/i)).toBeNull();
  });

  it("shows the no-favorite message when nothing earned a keep vote", () => {
    renderResults({
      kept: [{ id: "k1", title: "Legacy Card" }],
      destroyed: [{ id: "d1", title: "Dud" }],
      favorite_card_ids: [],
    });

    expect(screen.getByText("No table favorite this game.")).toBeTruthy();
    expect(screen.queryByText("Table favorite")).toBeNull();
  });

  it("renders old snapshots without favorite_card_ids safely", () => {
    renderResults({
      kept: [{ id: "k1", title: "Old Card" }],
      destroyed: [],
    });

    expect(screen.getByText("No table favorite this game.")).toBeTruthy();
    expect(screen.queryByText("Table favorite")).toBeNull();
  });

  it("never decorates a destroyed card even if a bad snapshot lists it", () => {
    renderResults({
      kept: [{ id: "k1", title: "Kept Card" }],
      destroyed: [{ id: "d1", title: "Doomed Card" }],
      favorite_card_ids: ["d1"],
    });

    expect(screen.queryByText("Table favorite")).toBeNull();
    expect(screen.getByText("No table favorite this game.")).toBeTruthy();
  });
});
