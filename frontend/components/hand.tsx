"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { CardTile } from "@/components/card";
import type { CardSnapshot, ClientMsg } from "@/lib/types";

interface HandProps {
  cards: CardSnapshot[];
  /** Is it this player's turn? Play controls only show when true. */
  canPlay: boolean;
  send: (msg: ClientMsg) => void;
}

export function Hand({ cards, canPlay, send }: HandProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // The player just picks a card and plays it. The interpreter reads the card
  // and, if a target is needed, the server replies with a prompt_choice that
  // the room page turns into a picker — so no zone/target dropdown here.
  function playSelected() {
    if (!selectedId) return;
    send({ type: "play", card_id: selectedId });
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
          <Button onClick={playSelected}>Play</Button>
        </div>
      )}
    </div>
  );
}
