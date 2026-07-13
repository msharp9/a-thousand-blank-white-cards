"use client";

import { useMemo, useState } from "react";
import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SketchCard, stableRotation } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import { resolvePlayerName } from "@/lib/players";
import type {
  CardSnapshot,
  PlayerSnapshot,
  SpectatorSnapshot,
} from "@/lib/types";

interface GalleryOverlayProps {
  cards: Record<string, CardSnapshot>;
  players: PlayerSnapshot[];
  spectators: SpectatorSnapshot[];
  roomCode: string;
  onClose: () => void;
}

const CARD_WIDTH = 164;
// One batch's worth of a wide gallery grid. Games can accumulate several
// hundred invented cards; rendering all of them at once would jank the
// scrim open. Instead of a scroll-triggered observer (extra moving parts,
// awkward to test deterministically) the grid caps at a batch and a "Show
// more" button appends the next one — simple, predictable, and still cheap
// for the common case of a few dozen cards.
const BATCH_SIZE = 60;

/**
 * Full-screen "The Deck" overlay (design's Gallery tab): every card ever
 * created in the game, sorted alphabetically by title (falling back to id
 * for untitled/duplicate titles, so ordering is stable across renders
 * regardless of the snapshot's own key order). Read-only — it renders
 * straight from the live gameState snapshot passed in by the caller, so WS
 * updates keep landing while it's open.
 */
export function GalleryOverlay({
  cards,
  players,
  spectators,
  roomCode,
  onClose,
}: GalleryOverlayProps) {
  const [visibleCount, setVisibleCount] = useState(BATCH_SIZE);

  const people = useMemo(
    () => [...players, ...spectators],
    [players, spectators],
  );

  const sorted = useMemo(() => {
    return Object.values(cards).sort((a, b) => {
      const at = (a.title || "").toLowerCase();
      const bt = (b.title || "").toLowerCase();
      if (at !== bt) return at < bt ? -1 : 1;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
  }, [cards]);

  const visible = sorted.slice(0, visibleCount);
  const remaining = sorted.length - visible.length;

  return (
    <div
      data-testid="gallery-scrim"
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-[rgba(20,18,14,0.55)] p-4"
      onClick={onClose}
    >
      <div
        className="flex w-full max-w-[1100px] flex-col overflow-hidden rounded-[18px] border-[3px] border-ink bg-card shadow-[8px_8px_0_rgba(26,26,26,0.8)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between border-b-2 border-ink px-5 py-3.5">
          <div>
            <h2 className="font-marker text-2xl">The Deck</h2>
            <p className="font-hand text-[15px] text-muted-foreground">
              {sorted.length} card{sorted.length === 1 ? "" : "s"} invented so
              far
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onClose}
            aria-label="Close gallery"
          >
            <XIcon />
          </Button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-5">
          {sorted.length === 0 ? (
            <p className="font-hand text-lg italic text-muted-foreground">
              No cards invented yet.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap justify-center gap-6">
                {visible.map((card) => (
                  <GalleryCard
                    key={card.id}
                    card={card}
                    artUrl={getCardArtUrl(roomCode, card)}
                    creator={resolvePlayerName(people, card.creator_id)}
                  />
                ))}
              </div>
              {remaining > 0 && (
                <div className="mt-6 flex justify-center">
                  <Button
                    variant="outline"
                    onClick={() => setVisibleCount((n) => n + BATCH_SIZE)}
                  >
                    Show more ({remaining} left)
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function GalleryCard({
  card,
  artUrl,
  creator,
}: {
  card: CardSnapshot;
  artUrl: string | null;
  creator?: string;
}) {
  const [hovered, setHovered] = useState(false);
  const rot = stableRotation(card.id, 6);

  return (
    <div className="flex w-[164px] flex-col items-center gap-1.5">
      <div
        style={{
          transform: hovered
            ? "rotate(0deg) scale(1.04)"
            : `rotate(${rot}deg) scale(1)`,
          transition: "transform 150ms ease-out",
          zIndex: hovered ? 10 : 0,
        }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <SketchCard card={card} w={CARD_WIDTH} artUrl={artUrl} />
      </div>
      {creator && (
        <p className="font-hand text-sm text-muted-foreground">by {creator}</p>
      )}
    </div>
  );
}
