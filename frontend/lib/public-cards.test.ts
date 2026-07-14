import { describe, expect, it } from "vitest";
import { publicCardIds } from "./public-cards";
import type { GameStateSnapshot, PlayerSnapshot } from "@/lib/types";

function player(
  overrides: Partial<PlayerSnapshot> & { id: string },
): PlayerSnapshot {
  return {
    name: overrides.id,
    score: 0,
    hand: [],
    in_play: [],
    connected: true,
    conditions: {},
    ...overrides,
  };
}

function state(overrides: Partial<GameStateSnapshot> = {}): GameStateSnapshot {
  return {
    room_code: "ABCD",
    phase: "playing",
    players: [],
    spectators: [],
    turn_index: 0,
    turn_number: 1,
    turn_order: [],
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

describe("publicCardIds", () => {
  it("includes in-play, discard, and house-rule cards", () => {
    const ids = publicCardIds(
      state({
        players: [
          player({ id: "p1", in_play: ["played1"] }),
          player({ id: "p2", in_play: ["played2"] }),
        ],
        discard: ["disc1"],
        house_rules: ["house1"],
      }),
    );
    expect([...ids].sort()).toEqual(
      ["disc1", "house1", "played1", "played2"].sort(),
    );
  });

  it("excludes cards in players' hands and the undrawn deck during play", () => {
    const ids = publicCardIds(
      state({
        phase: "playing",
        players: [
          player({ id: "p1", hand: ["secret1"], in_play: ["played1"] }),
        ],
        deck: ["deck1", "deck2"],
      }),
    );
    expect(ids.has("played1")).toBe(true);
    expect(ids.has("secret1")).toBe(false);
    expect(ids.has("deck1")).toBe(false);
    expect(ids.has("deck2")).toBe(false);
  });

  it("treats the deck-parked pre-made pool as public during setup only", () => {
    const setup = publicCardIds(
      state({ phase: "setup", deck: ["pool1", "pool2"] }),
    );
    expect(setup.has("pool1")).toBe(true);
    expect(setup.has("pool2")).toBe(true);

    const playing = publicCardIds(
      state({ phase: "playing", deck: ["pool1", "pool2"] }),
    );
    expect(playing.has("pool1")).toBe(false);
  });
});
