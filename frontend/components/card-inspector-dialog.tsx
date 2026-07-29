"use client";

import type { ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SketchCard, stableRotation } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import type { CardSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

interface CardInspectorDialogProps {
  card: CardSnapshot | null;
  roomCode: string;
  onDismiss: () => void;
}

/**
 * Shared click/tap-to-inspect overlay for any card mini rendered too small to
 * read comfortably (opponent revealed hands, in-front piles, reveal-dialog
 * cards). Renders one full-size SketchCard plus the title/description again
 * as plain selectable text, since an art-backed card shows the drawing (not
 * the rules text) on its face.
 */
export function CardInspectorDialog({
  card,
  roomCode,
  onDismiss,
}: CardInspectorDialogProps) {
  return (
    <Dialog
      open={Boolean(card)}
      onOpenChange={(open) => {
        if (!open) onDismiss();
      }}
    >
      <DialogContent className="animate-popin border-2 border-dashed border-ink bg-panel-paper shadow-none">
        {card && (
          <>
            <DialogHeader>
              <DialogTitle className="font-hand text-xl font-normal">
                {card.title || "Untitled"}
              </DialogTitle>
              <DialogDescription className="whitespace-pre-wrap font-hand text-base text-ink">
                {card.description || "No rules text."}
              </DialogDescription>
            </DialogHeader>
            <div className="flex justify-center py-1">
              <SketchCard
                card={card}
                w={260}
                showTape={false}
                rot={stableRotation(card.id, 3)}
                artUrl={getCardArtUrl(roomCode, card)}
              />
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * Accessible click/tap trigger wrapping a card mini (SketchCard rendered at a
 * hard-to-read width). Keeps the mini's own layout/animation classes on the
 * button itself so the wrapper is otherwise invisible in the surrounding
 * fan/row.
 */
export function CardInspectTrigger({
  card,
  onInspect,
  className,
  children,
}: {
  card: CardSnapshot;
  onInspect: (card: CardSnapshot) => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={`Inspect ${card.title || "card"}`}
      onClick={() => onInspect(card)}
      className={cn(
        "relative block cursor-pointer rounded-[7px] border-0 bg-transparent p-0 text-left focus-visible:ring-4 focus-visible:ring-primary/70 focus-visible:outline-none",
        className,
      )}
    >
      {children}
    </button>
  );
}
