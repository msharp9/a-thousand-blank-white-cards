// Player identity colors, cycled by player index (turn order). Every component
// that paints a player (avatar, score, panel border, target button) must derive
// the color from the same index so identities agree across the whole table.
//
// The hexes here are the light-theme values, kept for contexts that cannot
// resolve CSS custom properties (canvas pen strokes). playerColor() returns a
// var(--player-N) reference instead, whose light/dark values live in
// globals.css so identities stay recognizable in both themes.
export const PLAYER_COLORS = [
  "#E24A3B",
  "#2E5EAA",
  "#1F9E6B",
  "#E8A33D",
] as const;

export function playerColor(index: number): string {
  const n = PLAYER_COLORS.length;
  return `var(--player-${((index % n) + n) % n})`;
}

export function playerInitial(name: string): string {
  return name.trim().charAt(0).toUpperCase() || "?";
}
