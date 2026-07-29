import { describe, expect, it } from "vitest";
import type { CardSnapshot } from "./types";
import { getCardArtUrl } from "./art";

function card(overrides: Partial<CardSnapshot> = {}): CardSnapshot {
  return {
    id: "card-1",
    title: "Card",
    description: "Description",
    has_art: true,
    ...overrides,
  };
}

describe("getCardArtUrl", () => {
  it("uses the setup draft revision as an art cache key", () => {
    expect(getCardArtUrl("ROOM01", card({ draft_revision: 2 }))).toContain(
      "/rooms/ROOM01/cards/card-1/art?draft_revision=2",
    );
  });

  it("omits the revision query for stable cards", () => {
    expect(getCardArtUrl("ROOM01", card())).toMatch(
      /\/rooms\/ROOM01\/cards\/card-1\/art$/,
    );
  });
});
