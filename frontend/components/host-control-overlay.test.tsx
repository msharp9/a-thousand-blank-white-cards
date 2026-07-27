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

  it("God mode offers exact player-hand and ordered-deck moves", async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    const base = state();
    const godState = state({
      host_id: "spectator-host",
      players: base.players.map((player) =>
        player.id === "p2" ? { ...player, hand: ["bob-secret"] } : player,
      ),
      spectators: [{ id: "spectator-host", name: "Morgan" }],
      deck: ["d1", "d2"],
      deck_count: 2,
      cards: {
        ...base.cards,
        "bob-secret": {
          id: "bob-secret",
          title: "Bob Secret",
          description: "",
        },
        d1: { id: "d1", title: "First Draw", description: "" },
        d2: { id: "d2", title: "Deck Bottom", description: "" },
      },
    });
    render(
      <HostControlOverlay
        gameState={godState}
        godMode
        send={send}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Move a card" }));
    expect(screen.getByRole("option", { name: "Player hands" })).toBeTruthy();
    expect(
      screen.getByRole("option", { name: "Ordered deck (2)" }),
    ).toBeTruthy();

    await user.selectOptions(screen.getByLabelText("From"), "deck");
    expect(
      screen.getByRole("option", { name: "#1 (top) — First Draw" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("option", { name: "#2 (bottom) — Deck Bottom" }),
    ).toBeTruthy();

    await user.selectOptions(screen.getByLabelText("From"), "hand");
    await user.selectOptions(screen.getByLabelText("Source player"), "p2");
    await user.selectOptions(screen.getByLabelText("Card"), "p2|bob-secret");
    await user.selectOptions(screen.getByLabelText("To"), "discard");
    await user.click(screen.getByRole("button", { name: "Add card move" }));
    await user.click(
      screen.getByRole("button", { name: "Review proposal (1)" }),
    );
    await user.click(screen.getByRole("button", { name: "Propose changes" }));

    expect(send).toHaveBeenCalledWith({
      type: "admin_propose",
      actions: [
        {
          kind: "move_card",
          source_zone: "hand",
          card_id: "bob-secret",
          source_player_id: "p2",
          to_zone: "discard",
        },
      ],
    });
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
    expect(screen.getByText("Set Alice: Speak Only In Questions")).toBeTruthy();
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

  it("syncs untouched scores while preserving an explicit host edit", async () => {
    const user = userEvent.setup();
    const initial = state();
    const { rerender } = render(
      <HostControlOverlay
        gameState={initial}
        send={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const remotelyScored = state({
      players: initial.players.map((player) =>
        player.id === "p2" ? { ...player, score: 3 } : player,
      ),
    });
    rerender(
      <HostControlOverlay
        gameState={remotelyScored}
        send={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Bob score")).toHaveValue(3);
    expect(
      screen.getByRole("button", { name: "Review proposal (0)" }),
    ).toBeDisabled();

    await user.click(
      screen.getByRole("button", { name: "Add one point to Bob" }),
    );
    const changedAgain = state({
      players: initial.players.map((player) =>
        player.id === "p2" ? { ...player, score: 9 } : player,
      ),
    });
    rerender(
      <HostControlOverlay
        gameState={changedAgain}
        send={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Bob score")).toHaveValue(4);
    expect(
      screen.getByRole("button", { name: "Review proposal (1)" }),
    ).toBeEnabled();
  });
});
