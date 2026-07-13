import type { CardSnapshot } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * URL of a card's rendered artwork, or null when the card has none. The
 * endpoint serves immutable cache headers, so a plain <img src> is enough —
 * the browser cache handles reuse.
 */
export function getCardArtUrl(
  roomCode: string,
  card: CardSnapshot,
): string | null {
  return card.has_art
    ? `${API_URL}/rooms/${roomCode}/cards/${card.id}/art`
    : null;
}
