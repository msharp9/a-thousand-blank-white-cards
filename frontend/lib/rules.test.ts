import { describe, expect, it } from "vitest";
import {
  cannotPlaySentences,
  coreRuleSentences,
  drawRuleSentence,
  endConditionSentence,
  extraRuleSentences,
  handLimitSentence,
  humanizeRuleValue,
  playRuleSentence,
  skipPredicateSentence,
  turnTimerSentence,
  winConditionSentence,
} from "./rules";
import type { RulesSnapshot } from "@/lib/types";

function rules(overrides: Partial<RulesSnapshot> = {}): RulesSnapshot {
  return {
    draw: 1,
    play: 1,
    cannot_play: { draw: 1 },
    end_condition: { type: "deck_empty" },
    win_condition: { kind: "highest_points" },
    extra: {},
    ...overrides,
  };
}

describe("coreRuleSentences", () => {
  it("renders the default rules as the canonical example", () => {
    expect(coreRuleSentences(rules())).toEqual([
      "Draw 1 card at the start of your turn.",
      "Play 1 card on your turn.",
      "If you cannot play, draw 1 card.",
      "Game ends when the deck runs out, after the current turn.",
      "Winner: highest score.",
    ]);
  });

  it("appends optional modifiers and extra rules after the core lines", () => {
    const sentences = coreRuleSentences(
      rules({
        hand_limit: 7,
        turn_timer: 45,
        skip_predicate: "cursed_players",
        extra: { gravity_reversed: true, table_tax: 2 },
      }),
    );
    expect(sentences).toEqual([
      "Draw 1 card at the start of your turn.",
      "Play 1 card on your turn.",
      "If you cannot play, draw 1 card.",
      "Game ends when the deck runs out, after the current turn.",
      "Winner: highest score.",
      "Hand limit: 7 cards.",
      "Each turn has a 45-second time limit.",
      "Some turns may be skipped (Cursed Players).",
      "Gravity Reversed is in effect.",
      "Table Tax: 2.",
    ]);
  });

  it("never emits raw snake_case for known enums", () => {
    const sentences = coreRuleSentences(
      rules({
        end_condition: { type: "points_reached", threshold: 20 },
        win_condition: { kind: "first_to", threshold: 20 },
      }),
    );
    for (const sentence of sentences) {
      expect(sentence).not.toMatch(/[a-z]_[a-z]/);
    }
  });
});

describe("draw and play sentences", () => {
  it("handles zero, singular, and plural draw", () => {
    expect(drawRuleSentence(0)).toBe(
      "No cards are drawn at the start of your turn.",
    );
    expect(drawRuleSentence(1)).toBe("Draw 1 card at the start of your turn.");
    expect(drawRuleSentence(3)).toBe("Draw 3 cards at the start of your turn.");
  });

  it("handles zero, singular, and plural play", () => {
    expect(playRuleSentence(0)).toBe("No cards may be played on your turn.");
    expect(playRuleSentence(1)).toBe("Play 1 card on your turn.");
    expect(playRuleSentence(2)).toBe("Play 2 cards on your turn.");
  });
});

describe("cannotPlaySentences", () => {
  it("renders the draw fallback with pluralization", () => {
    expect(cannotPlaySentences({ draw: 1 })).toEqual([
      "If you cannot play, draw 1 card.",
    ]);
    expect(cannotPlaySentences({ draw: 2 })).toEqual([
      "If you cannot play, draw 2 cards.",
    ]);
  });

  it("drops empty or zero-draw fallbacks", () => {
    expect(cannotPlaySentences({})).toEqual([]);
    expect(cannotPlaySentences({ draw: 0 })).toEqual([]);
  });

  it("humanizes unknown fallback keys", () => {
    expect(cannotPlaySentences({ skip_turn: true, lose_points: 2 })).toEqual([
      "If you cannot play: Skip Turn.",
      "If you cannot play: Lose Points (2).",
    ]);
  });
});

describe("endConditionSentence", () => {
  it("covers every known end condition", () => {
    expect(endConditionSentence({ type: "deck_empty" })).toBe(
      "Game ends when the deck runs out, after the current turn.",
    );
    expect(endConditionSentence({ type: "empty_hand" })).toBe(
      "Game ends when a player runs out of cards in hand.",
    );
    expect(endConditionSentence({ type: "points_reached", threshold: 1 })).toBe(
      "Game ends when a player reaches 1 point.",
    );
    expect(
      endConditionSentence({ type: "points_reached", threshold: 25 }),
    ).toBe("Game ends when a player reaches 25 points.");
    expect(endConditionSentence({ type: "points_reached" })).toBe(
      "Game ends when a player reaches the target score.",
    );
    expect(endConditionSentence({ type: "now" })).toBe(
      "The game is ending now.",
    );
  });

  it("humanizes unknown end conditions", () => {
    expect(endConditionSentence({ type: "moon_phase" })).toBe(
      "Game ends: Moon Phase.",
    );
    expect(endConditionSentence({ type: "moon_phase" })).not.toContain(
      "moon_phase",
    );
  });
});

describe("winConditionSentence", () => {
  it("covers every known win condition", () => {
    expect(winConditionSentence({ kind: "highest_points" })).toBe(
      "Winner: highest score.",
    );
    expect(winConditionSentence({ kind: "lowest_points" })).toBe(
      "Winner: lowest score.",
    );
    expect(winConditionSentence({ kind: "first_to", threshold: 1 })).toBe(
      "Winner: first to 1 point.",
    );
    expect(winConditionSentence({ kind: "first_to", threshold: 30 })).toBe(
      "Winner: first to 30 points.",
    );
    expect(winConditionSentence({ kind: "first_to" })).toBe(
      "Winner: first to reach the target score.",
    );
    expect(winConditionSentence({ kind: "empty_hand" })).toBe(
      "Winner: first to empty their hand.",
    );
    expect(winConditionSentence({ kind: "last_standing" })).toBe(
      "Winner: last player standing.",
    );
    expect(winConditionSentence({ kind: "none" })).toBe(
      "Winner: no one — the game simply ends.",
    );
  });

  it("humanizes unknown win conditions", () => {
    const sentence = winConditionSentence({
      kind: "most_cursed",
      threshold: 3,
    });
    expect(sentence).toBe("Winner: Most Cursed (3).");
    expect(sentence).not.toContain("most_cursed");
  });
});

describe("optional modifier sentences", () => {
  it("pluralizes the hand limit", () => {
    expect(handLimitSentence(1)).toBe("Hand limit: 1 card.");
    expect(handLimitSentence(0)).toBe("Hand limit: 0 cards.");
    expect(handLimitSentence(7)).toBe("Hand limit: 7 cards.");
  });

  it("formats the turn timer", () => {
    expect(turnTimerSentence(90)).toBe("Each turn has a 90-second time limit.");
  });

  it("humanizes the skip predicate name", () => {
    expect(skipPredicateSentence("skip_if_cursed")).toBe(
      "Some turns may be skipped (Skip If Cursed).",
    );
  });
});

describe("extraRuleSentences", () => {
  it("renders scalars, lists, and nested objects as prose", () => {
    expect(
      extraRuleSentences({
        gravity_reversed: true,
        table_tax: 2,
        password: "open_sesame",
        banned_words: ["cat", "dog"],
        toll: { pay_points: 1 },
      }),
    ).toEqual([
      "Gravity Reversed is in effect.",
      "Table Tax: 2.",
      "Password: Open Sesame.",
      "Banned Words: cat, dog.",
      "Toll: Pay Points: 1.",
    ]);
  });

  it("skips false, null, empty-string, and empty-list values", () => {
    expect(
      extraRuleSentences({
        off: false,
        missing: null,
        blank: "",
        empty: [],
      }),
    ).toEqual([]);
  });
});

describe("humanizeRuleValue", () => {
  it("title-cases snake_case strings and keeps prose intact", () => {
    expect(humanizeRuleValue("double_points")).toBe("Double Points");
    expect(humanizeRuleValue("Speak only in rhymes")).toBe(
      "Speak only in rhymes",
    );
    expect(humanizeRuleValue([1, "two_words"])).toBe("1, Two Words");
  });
});
