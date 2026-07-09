"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { CardSnapshot, ClientMsg, GameStateSnapshot } from "@/lib/types";
import { CardTile } from "./card";
import { CreateCardDialog } from "./create-card-dialog";

interface SetupPhaseProps {
  gameState: GameStateSnapshot;
  myPlayerId: string;
  send: (msg: ClientMsg) => void;
  previewResult: { program?: string | null; snippet?: string | null; verdict: string } | null;
  isHost?: boolean;
}

const TARGET_AUTHORED = 5;

export function SetupPhase({ gameState, myPlayerId, send, previewResult, isHost }: SetupPhaseProps) {
  const [dialogOpen, setDialogOpen] = useState(false);

  const me = gameState.players.find((p) => p.id === myPlayerId);
  const myCards: CardSnapshot[] = (me?.hand ?? [])
    .map((id) => gameState.cards[id])
    .filter((c): c is CardSnapshot => Boolean(c));
  const myAuthored = Object.values(gameState.cards).filter(
    (c) => c.creator_id === myPlayerId || c.author_id === myPlayerId,
  );
  const remaining = Math.max(0, TARGET_AUTHORED - myAuthored.length);

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <div>
        <h2 className="text-xl font-bold">Setup — Author your cards</h2>
        <p className="text-sm text-muted-foreground">
          Write {TARGET_AUTHORED} cards to seed the deck. {remaining} to go.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">Cards you authored ({myAuthored.length})</p>
          <Button size="sm" onClick={() => setDialogOpen(true)}>
            Author a card
          </Button>
        </div>
        {myAuthored.length === 0 ? (
          <p className="text-xs italic text-muted-foreground">No cards yet — click “Author a card”.</p>
        ) : (
          <ScrollArea className="w-full">
            <div className="flex gap-2 pb-2">
              {myAuthored.map((card) => (
                <CardTile key={card.id} card={card} className="shrink-0" />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>

      {myCards.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">Your dealt hand ({myCards.length})</p>
          <ScrollArea className="w-full">
            <div className="flex gap-2 pb-2">
              {myCards.map((card) => (
                <CardTile key={card.id} card={card} className="shrink-0" />
              ))}
            </div>
          </ScrollArea>
        </div>
      )}

      {isHost && (
        <Button onClick={() => send({ type: "start" })} disabled={remaining > 0}>
          Start game
        </Button>
      )}

      <CreateCardDialog open={dialogOpen} onOpenChange={setDialogOpen} send={send} previewResult={previewResult} />
    </div>
  );
}
