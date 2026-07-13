// Player identity colors, cycled by player index (turn order). Every component
// that paints a player (avatar, score, panel border, target button) must derive
// the color from the same index so identities agree across the whole table.
export const PLAYER_COLORS = [
  "#E24A3B",
  "#2E5EAA",
  "#1F9E6B",
  "#E8A33D",
] as const;

export function playerColor(index: number): string {
  const n = PLAYER_COLORS.length;
  return PLAYER_COLORS[((index % n) + n) % n];
}

export function playerInitial(name: string): string {
  return name.trim().charAt(0).toUpperCase() || "?";
}
