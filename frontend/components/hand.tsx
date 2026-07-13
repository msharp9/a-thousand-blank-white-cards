"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { PlayBlankDialog } from "@/components/play-blank-dialog";
import { SketchCard } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import type { CardSnapshot, ClientMsg } from "@/lib/types";
import { cn } from "@/lib/utils";

interface HandProps {
  cards: CardSnapshot[];
  /** Is it this player's turn? Play controls only show when true. */
  canPlay: boolean;
  send: (msg: ClientMsg) => void;
  roomCode?: string;
}

export function Hand({ cards, canPlay, send, roomCode }: HandProps) {
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

  function playBlank(title: string, description: string, art?: string) {
    if (!selectedId) return;
    send({
      type: "play",
      card_id: selectedId,
      title,
      description,
      ...(art ? { art } : {}),
    });
    setSelectedId(null);
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="font-hand text-sm uppercase tracking-wide text-muted-foreground">
        Your hand
      </p>
      {cards.length === 0 ? (
        <p className="font-hand text-sm italic text-muted-foreground">
          No cards in hand.
        </p>
      ) : (
        <div className="flex items-end px-2 pb-2 pt-10">
          {cards.map((card, i) => {
            const isSelected = selectedId === card.id;
            return (
              <SketchCard
                key={card.id}
                card={card}
                w={130}
                rot={(i - (cards.length - 1) / 2) * 3}
                selectable={canPlay}
                selected={isSelected}
                onClick={() => setSelectedId(card.id)}
                artUrl={roomCode ? getCardArtUrl(roomCode, card) : null}
                className={cn(
                  i > 0 && "-ml-[34px]",
                  "hover:z-30",
                  isSelected && "z-30",
                  selectedId && !isSelected && "opacity-55",
                )}
              />
            );
          })}
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
