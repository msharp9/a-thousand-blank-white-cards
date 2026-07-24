"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SketchCard, stableRotation } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import type { CardSnapshot, HandRevealedMsg } from "@/lib/types";

interface HandRevealDialogProps {
  reveal: HandRevealedMsg | null;
  roomCode: string;
  onDismiss: () => void;
}

/**
 * One-shot hand reveal (reveal_hand, persistent=false): a dismissible modal
 * showing the revealed cards, sent only to the reveal's audience. Transient
 * by design — like the reaction window, it is lost on reconnect. The card
 * bodies ride the push itself (reveal.cards); redacted snapshots never carry
 * hidden hand content, so the shared registry cannot resolve these ids.
 */
export function HandRevealDialog({
  reveal,
  roomCode,
  onDismiss,
}: HandRevealDialogProps) {
  const cards: CardSnapshot[] = reveal
    ? reveal.card_ids
        .map((id) => reveal.cards[id])
        .filter((c): c is CardSnapshot => Boolean(c))
    : [];
  const who = reveal?.player_name || "A player";

  return (
    <Dialog
      open={Boolean(reveal)}
      onOpenChange={(open) => {
        if (!open) onDismiss();
      }}
    >
      <DialogContent className="animate-popin border-2 border-dashed border-ink bg-panel-paper shadow-none">
        <DialogHeader>
          <DialogTitle className="font-hand text-xl font-normal">
            👀 {who} reveals their hand
          </DialogTitle>
          <DialogDescription className="font-hand text-base">
            A one-time peek — it disappears when you close this.
          </DialogDescription>
        </DialogHeader>
        {cards.length === 0 ? (
          <p className="font-hand text-base italic text-muted-foreground">
            Their hand is empty.
          </p>
        ) : (
          <div className="flex flex-wrap items-end gap-2">
            {cards.map((card) => (
              <SketchCard
                key={card.id}
                card={card}
                w={110}
                showTape={false}
                rot={stableRotation(card.id, 4)}
                artUrl={getCardArtUrl(roomCode, card)}
              />
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
