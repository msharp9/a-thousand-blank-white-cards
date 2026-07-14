import type { GameStateSnapshot } from "@/lib/types";

/**
 * The set of card ids that are PUBLIC knowledge from a given snapshot.
 *
 * Played cards are open information: anything a player has in front of them
 * (`in_play`), the discard pile, and the center/house-rules zone. During setup
 * the shared pre-made pool is deliberately public too — the backend parks it in
 * `deck` while players author (see components/setup-phase.tsx), so `deck` counts
 * as public in that phase only.
 *
 * Deliberately EXCLUDED: cards sitting in players' hands and the undrawn deck
 * during play — those are secret, and leaking their titles/descriptions/art
 * would break the game's hidden information. When in doubt, a zone is excluded.
 */
export function publicCardIds(state: GameStateSnapshot): Set<string> {
  const ids = new Set<string>();
  for (const player of state.players) {
    for (const id of player.in_play) ids.add(id);
  }
  for (const id of state.discard) ids.add(id);
  for (const id of state.house_rules) ids.add(id);
  if (state.phase === "setup") {
    for (const id of state.deck) ids.add(id);
  }
  return ids;
}
