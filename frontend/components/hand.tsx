"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { CardTile } from "@/components/card";
import { PlayBlankDialog } from "@/components/play-blank-dialog";
import type { CardSnapshot, ClientMsg } from "@/lib/types";

interface HandProps {
  cards: CardSnapshot[];
  /** Is it this player's turn? Play controls only show when true. */
  canPlay: boolean;
  send: (msg: ClientMsg) => void;
}

export function Hand({ cards, canPlay, send }: HandProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [blankDialogOpen, setBlankDialogOpen] = useState(false);

  const selectedCard = cards.find((c) => c.id === selectedId) ?? null;

  // The player just picks a card and plays it. The interpreter reads the card
  // and, if a target is needed, the server replies with a prompt_choice that
  // the room page turns into a picker — so no zone/target dropdown here.
  //
  // A BLANK card is authored on play: instead of sending play immediately, open
  // the authoring dialog; on submit we send a play carrying the authored
  // title+description (the backend fills in the blank, then plays it).
  function playSelected() {
    if (!selectedId) return;
    if (selectedCard?.blank) {
      setBlankDialogOpen(true);
      return;
    }
    send({ type: "play", card_id: selectedId });
    setSelectedId(null);
  }

  function playBlank(title: string, description: string) {
    if (!selectedId) return;
    send({ type: "play", card_id: selectedId, title, description });
    setSelectedId(null);
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Your hand
      </p>
      {cards.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">
          No cards in hand.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {cards.map((card) => (
            <CardTile
              key={card.id}
              card={card}
              selectable={canPlay}
              onClick={() => setSelectedId(card.id)}
              className={
                selectedId === card.id
                  ? "border-primary ring-2 ring-primary/30"
                  : undefined
              }
            />
          ))}
        </div>
      )}

      {canPlay && selectedId && (
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={playSelected}>
            {selectedCard?.blank ? "Fill in & play" : "Play"}
          </Button>
        </div>
      )}

      <PlayBlankDialog
        open={blankDialogOpen}
        onOpenChange={setBlankDialogOpen}
        onPlay={playBlank}
      />
    </div>
  );
}
