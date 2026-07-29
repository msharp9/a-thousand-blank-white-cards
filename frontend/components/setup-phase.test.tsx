import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { GameStateSnapshot } from "@/lib/types";
import { SetupPhase } from "./setup-phase";

vi.mock("./sketch-card", () => ({
  SketchCard: ({ card }: { card: { title: string } }) => (
    <div>{card.title}</div>
  ),
}));

vi.mock("./create-card-dialog", () => ({
  CreateCardDialog: () => null,
}));

function setupState(): GameStateSnapshot {
  return {
    room_code: "ROOM01",
    mode: "online",
    phase: "setup",
    players: [
      {
        id: "p1",
        name: "Alice",
        score: 0,
        hand: [],
        in_play: [],
        connected: true,
        conditions: {},
      },
    ],
    spectators: [],
    turn_index: 0,
    turn_number: 0,
    turn_order: ["p1"],
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
    cards: {
      failed: {
        id: "failed",
        title: "Try Again",
        description: "Counter a card.",
        creator_id: "p1",
        draft_status: "failed",
        draft_reason: "Could not compile.",
      },
    },
    history_events: [],
    house_rules: [],
    hooks: [],
    has_drawn: false,
    can_pass: false,
    setup_progress: { p1: 0 },
    setup_draft_progress: {
      p1: { ready: 0, drafting: 0, failed: 1, total: 1 },
    },
    cards_to_author: 5,
    winner_ids: [],
    epilogue_result: null,
    log: [],
  };
}

describe("SetupPhase", () => {
  it("retries a failed draft in the same card slot", () => {
    const send = vi.fn();
    render(
      <SetupPhase
        gameState={setupState()}
        myPlayerId="p1"
        send={send}
        previewResult={null}
      />,
    );

    expect(screen.getAllByText(/1 failed/)).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(send).toHaveBeenCalledWith({
      type: "redraft_card",
      card_id: "failed",
      title: "Try Again",
      description: "Counter a card.",
    });
  });
});
