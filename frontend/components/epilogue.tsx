"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { getCardArtUrl } from "@/lib/art";
import type { CardSnapshot, ClientMsg } from "@/lib/types";
import { cn } from "@/lib/utils";
import { SketchCard, stableRotation } from "./sketch-card";

interface EpilogueProps {
  cards: CardSnapshot[];
  send: (msg: ClientMsg) => void;
  isHost: boolean;
  roomCode?: string;
}

type VoteChoice = "keep" | "destroy";

export function EpilogueView({ cards, send, isHost, roomCode }: EpilogueProps) {
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
  const keepCount = cards.filter((c) => votes[c.id] === "keep").length;
  const cutCount = cards.filter((c) => votes[c.id] === "destroy").length;

  return (
    <div className="mx-auto flex w-full max-w-[1000px] flex-col gap-5 pb-10">
      <div>
        <h2 className="text-center font-marker text-[38px] leading-[0.9]">
          The Epilogue
        </h2>
        <p className="mx-auto mt-1 max-w-[560px] text-center font-hand text-[19px] text-muted-foreground">
          Game over. Decide which cards deserve to live in the deck forever —
          and which get retired.
        </p>
        <p className="mt-2 text-center font-hand text-base text-muted-foreground">
          <span className="text-marker-green">{keepCount} kept</span> ·{" "}
          <span className="text-primary">{cutCount} cut</span> · {unvotedCount}{" "}
          to decide
        </p>
      </div>

      {!done && (
        <div className="flex flex-wrap justify-center gap-7">
          {cards.map((card) => {
            const choice = votes[card.id];
            const isCut = choice === "destroy";
            return (
              <div key={card.id} className="flex flex-col items-center gap-2.5">
                <div
                  className={cn(
                    "transition-all duration-200",
                    isCut && "opacity-35 grayscale-[0.9]",
                  )}
                >
                  <SketchCard
                    card={card}
                    w={160}
                    h={224}
                    rot={stableRotation(card.id, 3)}
                    artUrl={roomCode ? getCardArtUrl(roomCode, card) : null}
                  />
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className={cn(
                      "font-hand text-base font-normal sticker-shadow-sm",
                      choice === "keep" &&
                        "bg-marker-green text-white hover:bg-marker-green",
                    )}
                    onClick={() => vote(card.id, "keep")}
                  >
                    Keep
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className={cn(
                      "font-hand text-base font-normal sticker-shadow-sm",
                      isCut && "bg-primary text-white hover:bg-primary",
                    )}
                    onClick={() => vote(card.id, "destroy")}
                  >
                    Cut
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="flex items-center justify-center gap-3">
        {!done && (
          <Button size="lg" className="font-marker text-lg" onClick={markDone}>
            {unvotedCount > 0 ? "Skip remaining" : "Done voting"}
          </Button>
        )}
        {isHost && (
          <Button
            variant="outline"
            onClick={() => send({ type: "epilogue_finalize" })}
          >
            Finalize now
          </Button>
        )}
      </div>

      {done && (
        <p className="text-center font-hand text-lg text-muted-foreground">
          Done voting — waiting for other players…
        </p>
      )}
    </div>
  );
}
