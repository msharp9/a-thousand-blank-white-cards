import type { CardSnapshot } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * URL of a card's rendered artwork, or null when the card has none. The
 * endpoint serves immutable cache headers. Failed setup cards can replace
 * their art during revision, so their revision is part of the cache key.
 */
export function getCardArtUrl(
  roomCode: string,
  card: CardSnapshot,
): string | null {
  if (!card.has_art) return null;
  const revision = card.draft_revision
    ? `?draft_revision=${card.draft_revision}`
    : "";
  return `${API_URL}/rooms/${roomCode}/cards/${card.id}/art${revision}`;
}
