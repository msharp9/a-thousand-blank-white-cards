import { describe, expect, it } from "vitest";
import { PLAYER_COLORS, playerColor, playerInitial } from "./players";

describe("player display helpers", () => {
  it("cycles theme-aware identity colors and normalizes initials", () => {
    expect(playerColor(0)).toBe("var(--player-0)");
    expect(playerColor(PLAYER_COLORS.length)).toBe("var(--player-0)");
    expect(playerColor(-1)).toBe(`var(--player-${PLAYER_COLORS.length - 1})`);
    expect(playerInitial("  alice ")).toBe("A");
    expect(playerInitial("   ")).toBe("?");
  });
});
