"use client";

import { SketchCard, stableRotation } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import type { CardSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * One card face on a choice surface: the full SketchCard when a snapshot is
 * available, otherwise a card-shaped "details unavailable" placeholder built
 * from the supplied display name (never a raw id).
 */
export function CardFace({
  card,
  fallbackName,
  roomCode,
  w = 120,
}: {
  card?: CardSnapshot;
  fallbackName?: string;
  roomCode: string;
  w?: number;
}) {
  if (!card) {
    return (
      <SketchCard
        title={fallbackName || "Unknown card"}
        description="Card details unavailable"
        w={w}
        showTape={false}
      />
    );
  }
  return (
    <SketchCard
      card={card}
      w={w}
      showTape={false}
      rot={stableRotation(card.id, 2)}
      artUrl={getCardArtUrl(roomCode, card)}
    />
  );
}

/** The display name a card choice is announced with (never a raw id). */
export function cardChoiceLabel(
  card: CardSnapshot | undefined,
  fallbackName?: string,
): string {
  return card?.title || fallbackName || "unknown card";
}

/**
 * Full-card selection grid used by every card-choice surface (prompt_choice
 * card targets, single and multi card_pick). Renders complete SketchCard
 * faces as accessible buttons; selection state stays with the caller and
 * every activation reports only the card id.
 */
export function CardChoiceGrid({
  cardIds,
  faces,
  roomCode,
  names,
  selected,
  disabled,
  cardWidth = 120,
  onChoose,
}: {
  cardIds: string[];
  faces: Record<string, CardSnapshot>;
  roomCode: string;
  /** Fallback display names by card id (e.g. prompt choice labels). */
  names?: Record<string, string>;
  /** Present = multi-select mode: buttons toggle and expose aria-pressed. */
  selected?: string[];
  disabled?: boolean;
  cardWidth?: number;
  onChoose: (cardId: string) => void;
}) {
  const multi = selected !== undefined;
  return (
    <div className="flex flex-wrap justify-center gap-3">
      {cardIds.map((cardId) => {
        const card = faces[cardId];
        const label = cardChoiceLabel(card, names?.[cardId]);
        const active = multi && selected.includes(cardId);
        return (
          <button
            type="button"
            key={cardId}
            aria-pressed={multi ? active : undefined}
            aria-label={`Choose ${label}`}
            disabled={disabled}
            onClick={() => onChoose(cardId)}
            className={cn(
              "flex flex-col items-center gap-1 rounded-[12px] border-2 p-1.5 transition focus-visible:ring-4 focus-visible:ring-primary/70 focus-visible:outline-none",
              active
                ? "-translate-y-1 border-primary bg-primary/15 shadow-[0_4px_0_rgba(26,26,26,0.25)]"
                : "border-transparent hover:-translate-y-1 hover:border-ink/40",
              disabled && "opacity-60",
            )}
          >
            <CardFace
              card={card}
              fallbackName={names?.[cardId]}
              roomCode={roomCode}
              w={cardWidth}
            />
            {multi && (
              <span
                className={cn(
                  "font-hand text-sm leading-none",
                  active ? "text-primary" : "text-muted-foreground",
                )}
              >
                {active ? "Selected ✓" : "Tap to select"}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
