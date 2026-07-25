"use client";

import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { OverlayShell } from "@/components/overlay-shell";
import { SketchCard, stableRotation } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import { resolvePlayerName } from "@/lib/players";
import { publicCardIds } from "@/lib/public-cards";
import type { CardSnapshot, GameStateSnapshot } from "@/lib/types";
import { useCompactViewport } from "@/lib/use-compact-viewport";

interface GalleryOverlayProps {
  gameState: GameStateSnapshot;
  roomCode: string;
  onClose: () => void;
}

const DESKTOP_CARD_WIDTH = 164;
const MOBILE_CARD_WIDTH = 128;
// One batch's worth of a wide gallery grid. Games can accumulate several
// hundred invented cards; rendering all of them at once would jank the
// scrim open. Instead of a scroll-triggered observer (extra moving parts,
// awkward to test deterministically) the grid caps at a batch and a "Show
// more" button appends the next one — simple, predictable, and still cheap
// for the common case of a few dozen cards.
const BATCH_SIZE = 60;

/**
 * Full-screen "The Deck" overlay (design's Gallery tab): every PUBLICLY-KNOWN
 * card, sorted alphabetically by title (falling back to id for
 * untitled/duplicate titles, so ordering is stable across renders regardless
 * of the snapshot's own key order).
 *
 * Only public cards are shown — cards in play, in the discard, or in the
 * center/house-rules zone, plus the deliberately-public pre-made pool during
 * setup (see lib/public-cards.ts). Cards in players' hands and the undrawn
 * deck are secret and never surface here. Read-only: it renders straight from
 * the live gameState snapshot, so WS updates keep landing while it's open.
 */
export function GalleryOverlay({
  gameState,
  roomCode,
  onClose,
}: GalleryOverlayProps) {
  const compactViewport = useCompactViewport();
  const batchSize = compactViewport ? 24 : BATCH_SIZE;
  const [batchesShown, setBatchesShown] = useState(1);
  const visibleCount = batchesShown * batchSize;

  const people = useMemo(
    () => [...gameState.players, ...gameState.spectators],
    [gameState.players, gameState.spectators],
  );

  const sorted = useMemo(() => {
    const publicIds = publicCardIds(gameState);
    return Object.values(gameState.cards)
      .filter((card) => publicIds.has(card.id))
      .sort((a, b) => {
        const at = (a.title || "").toLowerCase();
        const bt = (b.title || "").toLowerCase();
        if (at !== bt) return at < bt ? -1 : 1;
        return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
      });
  }, [gameState]);

  const visible = sorted.slice(0, visibleCount);
  const remaining = sorted.length - visible.length;

  return (
    <OverlayShell
      scrimTestId="gallery-scrim"
      title="The Deck"
      subtitle={`${sorted.length} card${sorted.length === 1 ? "" : "s"} played so far`}
      closeLabel="Close gallery"
      onClose={onClose}
      panelClassName="max-w-[1100px]"
    >
      {sorted.length === 0 ? (
        <p className="font-hand text-lg italic text-muted-foreground">
          No cards played yet.
        </p>
      ) : (
        <>
          <div
            data-gallery-grid
            className="grid grid-cols-2 justify-items-center gap-x-3 gap-y-4 sm:flex sm:flex-wrap sm:justify-center sm:gap-6"
          >
            {visible.map((card) => (
              <GalleryCard
                key={card.id}
                card={card}
                artUrl={getCardArtUrl(roomCode, card)}
                creator={resolvePlayerName(people, card.creator_id)}
                compact={compactViewport}
              />
            ))}
          </div>
          {remaining > 0 && (
            <div className="mt-6 flex justify-center">
              <Button
                variant="outline"
                onClick={() => setBatchesShown((count) => count + 1)}
              >
                Show more ({remaining} left)
              </Button>
            </div>
          )}
        </>
      )}
    </OverlayShell>
  );
}

function GalleryCard({
  card,
  artUrl,
  creator,
  compact,
}: {
  card: CardSnapshot;
  artUrl: string | null;
  creator?: string;
  compact: boolean;
}) {
  const [hovered, setHovered] = useState(false);
  const rot = stableRotation(card.id, 6);

  return (
    <div className="flex w-[128px] flex-col items-center gap-1.5 sm:w-[164px]">
      <div
        style={{
          transform:
            hovered && !compact
              ? "rotate(0deg) scale(1.04)"
              : `rotate(${rot}deg) scale(1)`,
          transition: "transform 150ms ease-out",
          zIndex: hovered && !compact ? 10 : 0,
        }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <SketchCard
          card={card}
          w={compact ? MOBILE_CARD_WIDTH : DESKTOP_CARD_WIDTH}
          artUrl={artUrl}
        />
      </div>
      {creator && (
        <p className="font-hand text-sm text-muted-foreground">by {creator}</p>
      )}
    </div>
  );
}
