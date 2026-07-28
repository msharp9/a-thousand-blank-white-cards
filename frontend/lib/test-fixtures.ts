import type { PlayerSnapshot } from "@/lib/types";

export function makePlayer(
  overrides: Partial<PlayerSnapshot> = {},
): PlayerSnapshot {
  return {
    id: "p1",
    name: "Alice",
    score: 0,
    hand: [],
    in_play: [],
    connected: true,
    conditions: {},
    ...overrides,
  };
}
