"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CardTile } from "@/components/card";
import type { CardSnapshot, ClientMsg, Placement } from "@/lib/types";

interface HandProps {
  cards: CardSnapshot[];
  /** Is it this player's turn? Play controls only show when true. */
  canPlay: boolean;
  /** Other players for the "player" placement target. */
  otherPlayers: Array<{ id: string; name: string }>;
  send: (msg: ClientMsg) => void;
}

export function Hand({ cards, canPlay, otherPlayers, send }: HandProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [zone, setZone] = useState<Placement["zone"]>("self");
  const [targetPlayerId, setTargetPlayerId] = useState<string | undefined>(
    undefined,
  );

  function playSelected() {
    if (!selectedId) return;
    const placement: Placement = { zone };
    if (zone === "player" && targetPlayerId) {
      placement.target_player_id = targetPlayerId;
    }
    send({ type: "play", card_id: selectedId, placement });
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
          <Select
            value={zone}
            onValueChange={(v) => setZone(v as Placement["zone"])}
          >
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Placement" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="self">In front of me</SelectItem>
              <SelectItem value="player">Target a player</SelectItem>
              <SelectItem value="center">Center / house rule</SelectItem>
            </SelectContent>
          </Select>

          {zone === "player" && (
            <Select
              value={targetPlayerId ?? null}
              onValueChange={(v) =>
                setTargetPlayerId((v as string | null) ?? undefined)
              }
            >
              <SelectTrigger className="w-40">
                <SelectValue placeholder="Pick player" />
              </SelectTrigger>
              <SelectContent>
                {otherPlayers.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          <Button
            onClick={playSelected}
            disabled={zone === "player" && !targetPlayerId}
          >
            Play
          </Button>
        </div>
      )}
    </div>
  );
}
