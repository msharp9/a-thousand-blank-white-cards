import { describe, expect, it } from "vitest";
import { PLAYER_COLORS, playerColor, playerInitial } from "./players";

describe("player display helpers", () => {
  it("cycles identity colors and normalizes initials", () => {
    expect(playerColor(PLAYER_COLORS.length)).toBe(PLAYER_COLORS[0]);
    expect(playerColor(-1)).toBe(PLAYER_COLORS.at(-1));
    expect(playerInitial("  alice ")).toBe("A");
    expect(playerInitial("   ")).toBe("?");
  });
});
