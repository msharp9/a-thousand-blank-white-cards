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

/** Resolve an id (e.g. a card's `creator_id`) to a display name, or undefined
 * when the id is absent or nobody in `people` matches (e.g. a player who left
 * the room). Callers typically pass `[...players, ...spectators]` so both
 * active players and late-joining spectators resolve. */
export function resolvePlayerName(
  people: { id: string; name: string }[],
  id: string | null | undefined,
): string | undefined {
  if (!id) return undefined;
  return people.find((p) => p.id === id)?.name;
}
