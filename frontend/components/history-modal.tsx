"use client";

import { useMemo } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { SketchCard } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import type { GameStateSnapshot, HistoryEventSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

interface HistoryModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  gameState: GameStateSnapshot;
  roomCode: string;
}

const MINI_WIDTH = 52;

/**
 * The discard pile's "Everything Played" history modal (docs/design/
 * handoff-README.md section 2): every play, newest first, regardless of which
 * zone it landed in (discard / center / in front of a player) — this reads
 * `history_events` (kind "play"), not the `discard` zone list, so a card that
 * stayed in play still shows up here.
 *
 * `by`/`target`/`turn` metadata renders only when the event actually carries
 * it: a play with no known target (e.g. a self-only or "everyone" effect the
 * engine didn't record a chooser for) simply omits the target, rather than
 * guessing one.
 */
export function HistoryModal({
  open,
  onOpenChange,
  gameState,
  roomCode,
}: HistoryModalProps) {
  const playerName = useMemo(() => {
    const names = new Map<string, string>();
    for (const player of gameState.players) names.set(player.id, player.name);
    for (const spectator of gameState.spectators)
      names.set(spectator.id, spectator.name);
    return (id: string | null | undefined) =>
      id ? (names.get(id) ?? id) : undefined;
  }, [gameState.players, gameState.spectators]);

  const plays = useMemo(
    () =>
      gameState.history_events
        .filter((event): event is HistoryEventSnapshot & { card_id: string } =>
          Boolean(event.kind === "play" && event.card_id),
        )
        .slice()
        .reverse(),
    [gameState.history_events],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100vh-2rem)] sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-marker text-xl font-normal">
            Everything Played
          </DialogTitle>
        </DialogHeader>
        {plays.length === 0 ? (
          <p className="font-hand text-base text-muted-foreground">
            No cards played yet.
          </p>
        ) : (
          <ScrollArea className="max-h-[60vh]">
            <ul className="flex flex-col gap-3 pr-3">
              {plays.map((event) => {
                const card = gameState.cards[event.card_id];
                const by = playerName(event.actor_id);
                const targetNames = event.target_player_ids
                  .map((id) => playerName(id))
                  .filter((name): name is string => Boolean(name));
                const target =
                  targetNames.length > 1
                    ? "Everyone"
                    : (targetNames[0] ?? undefined);
                return (
                  <li
                    key={event.sequence}
                    className={cn(
                      "flex items-start gap-3 rounded-[10px] border-[1.5px] border-ink/20 bg-card px-3 py-2.5",
                    )}
                  >
                    <SketchCard
                      card={card}
                      title={card?.title ?? "Unknown card"}
                      description={card?.description ?? ""}
                      w={MINI_WIDTH}
                      showTape={false}
                      artUrl={card ? getCardArtUrl(roomCode, card) : null}
                      className="shrink-0"
                    />
                    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <p className="truncate font-hand text-lg leading-tight">
                        {card?.title || "Unknown card"}
                      </p>
                      {card?.description && (
                        <p className="line-clamp-2 text-sm text-muted-foreground">
                          {card.description}
                        </p>
                      )}
                    </div>
                    <div className="shrink-0 text-right font-hand text-sm text-muted-foreground">
                      {by && (
                        <p>
                          <span className="font-semibold text-foreground">
                            {by}
                          </span>
                          {target && <> &rarr; {target}</>}
                        </p>
                      )}
                      {event.turn != null && <p>Turn {event.turn}</p>}
                    </div>
                  </li>
                );
              })}
            </ul>
          </ScrollArea>
        )}
      </DialogContent>
    </Dialog>
  );
}
