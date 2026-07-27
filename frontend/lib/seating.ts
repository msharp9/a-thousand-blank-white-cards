// Viewer-relative seat projection for the Play Table. Physical seat order and
// the mutable turn order must agree from every seated viewer's perspective:
// the viewer anchors the bottom of the table and opponents render far-left to
// far-right in the turn sequence after the viewer, so play flows
// viewer -> far-left -> ... -> far-right -> viewer. When a card reverses or
// scrambles turn_order the projection changes with it; the backend roster is
// never reordered.

/**
 * Canonical seat order: unique turn-order ids that exist in the roster,
 * followed by any roster ids a malformed/legacy turn_order omitted, in roster
 * order. Deterministic for any input.
 */
export function canonicalSeatOrder(
  turnOrder: string[],
  rosterIds: string[],
): string[] {
  const roster = new Set(rosterIds);
  const seen = new Set<string>();
  const ids: string[] = [];
  for (const id of [...turnOrder, ...rosterIds]) {
    if (roster.has(id) && !seen.has(id)) {
      seen.add(id);
      ids.push(id);
    }
  }
  return ids;
}

/**
 * Opponent ids left-to-right for a viewer. A seated viewer is excluded and the
 * order rotates to start at their turn-order successor (far-left) and end at
 * their predecessor (far-right), without reversing. A spectator viewer gets
 * the full canonical order unchanged.
 */
export function projectSeats(
  turnOrder: string[],
  rosterIds: string[],
  viewerId: string,
): string[] {
  const ids = canonicalSeatOrder(turnOrder, rosterIds);
  const i = ids.indexOf(viewerId);
  if (i === -1) return ids;
  return [...ids.slice(i + 1), ...ids.slice(0, i)];
}

/** Which table edge a rendered opponent seat sits on, relative to a seated
 * viewer. Spectators have no seat, so no edge applies. */
export type SeatEdge = "left" | "right" | "both";

export function seatEdge(
  index: number,
  count: number,
  viewerSeated: boolean,
): SeatEdge | null {
  if (!viewerSeated || count < 1) return null;
  if (count === 1) return "both";
  if (index === 0) return "left";
  if (index === count - 1) return "right";
  return null;
}

export const SEAT_EDGE_LABELS: Record<SeatEdge, string> = {
  left: "Left · next seat",
  right: "Right · previous seat",
  both: "Left & right neighbor",
};
