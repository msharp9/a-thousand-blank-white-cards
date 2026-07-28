import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { conditionLabel, GameTable } from "./game-table";
import { playerColor } from "@/lib/players";
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
    expect(conditionLabel("skip_next", true)).toBe("Skip Next");
    expect(conditionLabel("extra_turn", true)).toBe("Extra Turn");
  });

  it("humanizes free-form keys and appends numeric stacks", () => {
    expect(conditionLabel("on_fire", true)).toBe("On Fire");
    expect(conditionLabel("poisoned", 3)).toBe("Poisoned · 3 stacks");
  });

  it("appends the TTL, singular and plural, combined with stacks", () => {
    expect(conditionLabel("poisoned", true, 1)).toBe(
      "Poisoned · for 1 more turn",
    );
    expect(conditionLabel("poisoned", 3, 2)).toBe(
      "Poisoned · 3 stacks · for 2 more turns",
    );
  });

  it("renders a TTL of 0 as the condition's last active turn", () => {
    expect(conditionLabel("poisoned", true, 0)).toBe(
      "Poisoned · for the rest of this turn",
    );
  });
});

function fourSeatState(turnOrder: string[]): GameStateSnapshot {
  return baseState({
    players: [
      player({ id: "a", name: "Alice" }),
      player({ id: "b", name: "Bob" }),
      player({ id: "me", name: "Me" }),
      player({ id: "c", name: "Cleo" }),
    ],
    turn_order: turnOrder,
  });
}

function railOrder(container: HTMLElement): string[] {
  return [...container.querySelectorAll("[data-seat-drop]")].map(
    (seat) => seat.getAttribute("data-seat-drop") ?? "",
  );
}

describe("GameTable seat projection", () => {
  it("anchors the viewer and rotates opponents successor-first", () => {
    const { container } = render(
      <GameTable
        gameState={fourSeatState(["me", "a", "b", "c"])}
        myPlayerId="me"
      />,
    );
    expect(railOrder(container)).toEqual(["a", "b", "c"]);
  });

  it("projects a mid-order viewer without reversing", () => {
    const { container } = render(
      <GameTable
        gameState={fourSeatState(["a", "b", "me", "c"])}
        myPlayerId="me"
      />,
    );
    expect(railOrder(container)).toEqual(["c", "a", "b"]);
  });

  it("reprojects when the turn order reverses, roster untouched", () => {
    const state = fourSeatState(["c", "me", "b", "a"]);
    const { container } = render(
      <GameTable gameState={state} myPlayerId="me" />,
    );
    expect(railOrder(container)).toEqual(["b", "a", "c"]);
    expect(state.players.map((p) => p.id)).toEqual(["a", "b", "me", "c"]);
  });

  it("normalizes malformed turn orders deterministically", () => {
    const { container } = render(
      <GameTable
        gameState={fourSeatState(["ghost", "c", "c", "me"])}
        myPlayerId="me"
      />,
    );
    expect(railOrder(container)).toEqual(["a", "b", "c"]);
  });

  it("shows a spectator every seat in canonical order", () => {
    const state = fourSeatState(["a", "b", "me", "c"]);
    state.spectators = [{ id: "spec", name: "Watcher" }];
    const { container } = render(
      <GameTable gameState={state} myPlayerId="spec" />,
    );
    expect(railOrder(container)).toEqual(["a", "b", "me", "c"]);
    expect(container.querySelector("[data-seat-edge]")).toBeNull();
  });

  it("keeps identity colors keyed to the roster index", () => {
    const { container } = render(
      <GameTable
        gameState={fourSeatState(["a", "b", "me", "c"])}
        myPlayerId="me"
      />,
    );
    const seats = [
      ...container.querySelectorAll<HTMLElement>("[data-seat-drop]"),
    ];
    expect(seats[0].style.border).toContain(playerColor(3));
    expect(seats[1].style.border).toContain(playerColor(0));
    expect(seats[2].style.border).toContain(playerColor(1));
  });

  it("labels the far-left and far-right seats relative to the viewer", () => {
    const { container } = render(
      <GameTable
        gameState={fourSeatState(["a", "b", "me", "c"])}
        myPlayerId="me"
      />,
    );
    const edges = [...container.querySelectorAll("[data-seat-drop]")].map(
      (seat) => seat.getAttribute("data-seat-edge"),
    );
    expect(edges).toEqual(["left", null, "right"]);
    expect(screen.getByText("Left · next seat")).toBeTruthy();
    expect(screen.getByText("Right · previous seat")).toBeTruthy();
    expect(
      screen.getByRole("group", { name: "Cleo — Left · next seat" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("group", { name: "Bob — Right · previous seat" }),
    ).toBeTruthy();
  });

  it("labels a sole opponent as both neighbors", () => {
    render(<GameTable gameState={baseState()} myPlayerId="me" />);
    expect(screen.getByText("Left & right neighbor")).toBeTruthy();
    expect(
      screen.getByRole("group", { name: "Alice — Left & right neighbor" }),
    ).toBeTruthy();
  });

  it("marks the players[turn_index] seat with a badge and solid border", () => {
    const state = fourSeatState(["a", "b", "me", "c"]);
    state.turn_index = 3;
    const { container } = render(
      <GameTable gameState={state} myPlayerId="me" />,
    );
    const cleoSeat = screen.getByRole("group", {
      name: "Cleo — Left · next seat",
    });
    expect(cleoSeat.getAttribute("data-active-turn")).toBe("true");
    expect(
      cleoSeat.querySelector("[data-current-turn-badge]")?.textContent,
    ).toBe("Current turn");
    expect(cleoSeat.style.border).toContain("solid");
    expect(cleoSeat.style.boxShadow).toContain(playerColor(3));
    const inactiveSeats = [
      ...container.querySelectorAll<HTMLElement>(
        "[data-seat-drop]:not([data-active-turn])",
      ),
    ];
    expect(inactiveSeats).toHaveLength(2);
    for (const seat of inactiveSeats) {
      expect(seat.style.border).toContain("dashed");
      expect(seat.querySelector("[data-current-turn-badge]")).toBeNull();
    }
  });

  it("keeps the badge distinct from the seat-edge label on one seat", () => {
    const state = fourSeatState(["a", "b", "me", "c"]);
    state.turn_index = 3;
    render(<GameTable gameState={state} myPlayerId="me" />);
    const cleoSeat = screen.getByRole("group", {
      name: "Cleo — Left · next seat",
    });
    expect(cleoSeat.querySelector("[data-seat-edge-label]")?.textContent).toBe(
      "Left · next seat",
    );
    expect(cleoSeat.querySelector("[data-current-turn-badge]")).not.toBeNull();
  });

  it("keeps an offline active seat dimmed but still badged", () => {
    const state = fourSeatState(["a", "b", "me", "c"]);
    state.turn_index = 0;
    state.players[0].connected = false;
    render(<GameTable gameState={state} myPlayerId="me" />);
    const aliceSeat = screen.getByRole("group", { name: "Alice" });
    expect(aliceSeat.className).toContain("opacity-50");
    expect(aliceSeat.textContent).toContain("· offline");
    expect(aliceSeat.querySelector("[data-current-turn-badge]")).not.toBeNull();
  });

  it("keeps eliminated and offline seats visible", () => {
    const state = fourSeatState(["me", "a", "b", "c"]);
    state.players[0].eliminated = true;
    state.players[1].connected = false;
    const { container } = render(
      <GameTable gameState={state} myPlayerId="me" />,
    );
    expect(railOrder(container)).toEqual(["a", "b", "c"]);
    expect(screen.getByText("· eliminated")).toBeTruthy();
    expect(screen.getByText("· offline")).toBeTruthy();
  });
});

describe("GameTable condition badges", () => {
  it("shows badges under a conditioned opponent's seat", () => {
    const state = baseState();
    state.players[0].conditions = { skip_next: true, poisoned: 2 };
    state.players[0].condition_ttls = { poisoned: 2 };
    render(<GameTable gameState={state} myPlayerId="me" />);
    expect(screen.getByText("Skip Next")).toBeTruthy();
    expect(
      screen.getByText("Poisoned · 2 stacks · for 2 more turns"),
    ).toBeTruthy();
  });

  it("renders no badge for falsy-valued (toggled-off) conditions", () => {
    const state = baseState();
    state.players[0].conditions = { frozen: false };
    render(<GameTable gameState={state} myPlayerId="me" />);
    expect(screen.queryByText(/frozen/)).toBeNull();
  });
});
