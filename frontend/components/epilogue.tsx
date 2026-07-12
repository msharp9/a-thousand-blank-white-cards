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
  isHost: boolean;
}

type VoteChoice = "keep" | "destroy";

export function EpilogueView({ cards, send, isHost }: EpilogueProps) {
  const [votes, setVotes] = useState<Record<string, VoteChoice>>({});
  const [done, setDone] = useState(false);

  function vote(cardId: string, choice: VoteChoice) {
    setVotes((prev) => ({ ...prev, [cardId]: choice }));
    send({ type: "epilogue_vote", card_id: cardId, keep: choice === "keep" });
  }

  function markDone() {
    setDone(true);
    send({ type: "epilogue_done" });
  }

  // Unvoted cards abstain (never block finalizing), so Done is always
  // enabled — its label just adapts to whether anything would be skipped.
  const unvotedCount = cards.filter((c) => votes[c.id] === undefined).length;

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <div>
        <h2 className="text-xl font-bold">Epilogue — Vote on the Cards</h2>
        <p className="text-sm text-muted-foreground">
          Keep the good ones (they join the permanent deck) or destroy the rest.
        </p>
      </div>

      {!done && (
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
      )}

      <div className="flex items-center justify-center gap-3">
        {!done && (
          <Button onClick={markDone}>
            {unvotedCount > 0 ? "Skip remaining" : "Done voting"}
          </Button>
        )}
        {isHost && (
          <Button
            variant="secondary"
            onClick={() => send({ type: "epilogue_finalize" })}
          >
            Finalize now
          </Button>
        )}
      </div>

      {done && (
        <p className="text-center text-sm text-muted-foreground">
          Done voting — waiting for other players…
        </p>
      )}
    </div>
  );
}
