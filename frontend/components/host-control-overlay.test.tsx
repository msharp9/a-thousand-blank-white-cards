import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HostControlOverlay } from "./host-control-overlay";
import type { GameStateSnapshot } from "@/lib/types";

function state(overrides: Partial<GameStateSnapshot> = {}): GameStateSnapshot {
  return {
    room_code: "ABCD",
    mode: "in_person",
    phase: "playing",
    players: [
      {
        id: "host",
        name: "Alice",
        score: 2,
        hand: ["secret"],
        in_play: [],
        connected: true,
        conditions: {},
      },
      {
        id: "p2",
        name: "Bob",
        score: 0,
        hand: [],
        in_play: [],
        connected: true,
        conditions: {},
      },
    ],
    spectators: [],
    turn_index: 0,
    turn_number: 1,
    turn_order: ["host", "p2"],
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
    deck_count: 4,
    discard: [],
    exiled: [],
    cards: {
      secret: {
        id: "secret",
        title: "Opponent secret",
        description: "Never list this as a movable source.",
      },
    },
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

describe("HostControlOverlay", () => {
  it("builds and submits an atomic score proposal", async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    const onClose = vi.fn();
    render(
      <HostControlOverlay gameState={state()} send={send} onClose={onClose} />,
    );

    await user.click(
      screen.getByRole("button", { name: "Add one point to Bob" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Review proposal (1)" }),
    );
    expect(screen.getByText("Set Bob’s score to 1")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Propose changes" }));
    expect(send).toHaveBeenCalledWith({
      type: "admin_propose",
      actions: [{ kind: "set_score", player_id: "p2", score: 1 }],
    });
    expect(onClose).toHaveBeenCalled();
  });

  it("offers only public card sources plus deck top/bottom", async () => {
    const user = userEvent.setup();
    render(
      <HostControlOverlay
        gameState={state()}
        send={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Move a card" }));
    expect(screen.queryByText("Opponent secret")).toBeNull();
    expect(
      screen.getByRole("option", { name: "Hidden deck (4)" }),
    ).toBeTruthy();
    await user.selectOptions(screen.getByLabelText("From"), "deck");
    expect(screen.getByRole("option", { name: "Top card" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Bottom card" })).toBeTruthy();
  });

  it("turns a human-readable condition name into a valid on/off condition", async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    render(
      <HostControlOverlay gameState={state()} send={send} onClose={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "Conditions" }));
    expect(screen.queryByPlaceholderText(/Condition key/)).toBeNull();
    expect(
      screen.getByText(/Enter any condition your table agreed to/),
    ).toBeTruthy();
    await user.type(
      screen.getByLabelText("Condition name"),
      "Speak Only in Questions",
    );
    await user.click(
      screen.getByRole("button", { name: "Add condition change" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Review proposal (1)" }),
    );
    expect(screen.getByText("Set Alice: speak only in questions")).toBeTruthy();
    expect(screen.queryByText(/speak_only_in_questions/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Propose changes" }));

    expect(send).toHaveBeenCalledWith({
      type: "admin_propose",
      actions: [
        {
          kind: "set_condition",
          player_id: "host",
          key: "speak_only_in_questions",
          value: true,
        },
      ],
    });
  });

  it("submits score and winner corrections together from results", async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    render(
      <HostControlOverlay
        gameState={state({ phase: "results", winner_ids: ["host"] })}
        send={send}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Bob" }));
    await user.click(
      screen.getByRole("button", { name: "Review proposal (1)" }),
    );
    await user.click(screen.getByRole("button", { name: "Propose changes" }));
    expect(send).toHaveBeenCalledWith({
      type: "admin_propose",
      actions: [{ kind: "set_result_winners", winner_ids: ["host", "p2"] }],
    });
  });
});
