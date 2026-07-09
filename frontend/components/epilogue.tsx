"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { CardSnapshot, ClientMsg } from "@/lib/types";
import { cn } from "@/lib/utils";
import { CardTile } from "./card";

interface EpilogueProps {
  cards: CardSnapshot[];
  send: (msg: ClientMsg) => void;
}

type VoteChoice = "keep" | "destroy";

export function EpilogueView({ cards, send }: EpilogueProps) {
  const [votes, setVotes] = useState<Record<string, VoteChoice>>({});

  function vote(cardId: string, choice: VoteChoice) {
    setVotes((prev) => ({ ...prev, [cardId]: choice }));
    send({ type: "epilogue_vote", card_id: cardId, keep: choice === "keep" });
  }

  const allVoted =
    cards.length > 0 && cards.every((c) => votes[c.id] !== undefined);

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <div>
        <h2 className="text-xl font-bold">Epilogue — Vote on the Cards</h2>
        <p className="text-sm text-muted-foreground">
          Keep the good ones (they join the permanent deck) or destroy the rest.
        </p>
      </div>

      <div className="flex flex-col gap-4">
        {cards.map((card) => {
          const choice = votes[card.id];
          return (
            <div key={card.id} className="flex items-center gap-4">
              <CardTile card={card} />
              <div className="flex flex-col gap-2">
                <Button
                  variant={choice === "keep" ? "default" : "outline"}
                  size="sm"
                  onClick={() => vote(card.id, "keep")}
                >
                  Keep
                </Button>
                <Button
                  variant={choice === "destroy" ? "destructive" : "outline"}
                  size="sm"
                  onClick={() => vote(card.id, "destroy")}
                >
                  Destroy
                </Button>
              </div>
              {choice && (
                <Badge
                  variant={choice === "keep" ? "default" : "destructive"}
                  className={cn("ml-auto")}
                >
                  {choice}
                </Badge>
              )}
            </div>
          );
        })}
      </div>

      {allVoted && (
        <p className="text-center text-sm text-muted-foreground">
          All votes cast — waiting for other players…
        </p>
      )}
    </div>
  );
}
