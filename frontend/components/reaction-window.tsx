"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SketchCard } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import type { CardSnapshot, ClientMsg, PendingPlaySnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ReactionWindowProps {
  pending: PendingPlaySnapshot;
  /** The suspended card (resolved from the state snapshot's cards registry). */
  pendingCard: CardSnapshot | undefined;
  actorName: string;
  /** Reaction cards (canonical.trigger === "on_reaction") in MY hand. */
  myReactionCards: CardSnapshot[];
  isActor: boolean;
  isSpectator: boolean;
  send: (msg: ClientMsg) => void;
  roomCode: string;
}

/** Live ms-remaining against the server deadline, ticked every 250ms. The
 * server deadline is authoritative (a timeout there resolves the play whether
 * or not this countdown agrees); the bar is purely informational. */
function useCountdown(deadlineEpochMs: number): number {
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, deadlineEpochMs - Date.now()),
  );
  useEffect(() => {
    const tick = () => setRemaining(Math.max(0, deadlineEpochMs - Date.now()));
    tick();
    const interval = setInterval(tick, 250);
    return () => clearInterval(interval);
  }, [deadlineEpochMs]);
  return remaining;
}

function CountdownBar({ deadlineEpochMs }: { deadlineEpochMs: number }) {
  const remaining = useCountdown(deadlineEpochMs);
  // First-seen remaining = 100%; the deadline moves when a reactor claims the
  // window (timer restart), so re-anchor whenever it grows. Render-time state
  // adjustment is React's sanctioned derived-state pattern.
  const [total, setTotal] = useState(remaining);
  if (remaining > total) setTotal(remaining);
  const pct = total > 0 ? (remaining / total) * 100 : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-2.5 flex-1 overflow-hidden rounded-full border border-ink bg-white">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-200",
            pct > 35 ? "bg-marker-green" : "bg-destructive",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="font-mono text-sm tabular-nums">
        {Math.ceil(remaining / 1000)}s
      </span>
    </div>
  );
}

export function ReactionWindow({
  pending,
  pendingCard,
  actorName,
  myReactionCards,
  isActor,
  isSpectator,
  send,
  roomCode,
}: ReactionWindowProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Passed windows, tracked locally: after passing you drop to the waiting
  // banner. Keyed by window_id so a new window re-prompts.
  const [passedWindowId, setPassedWindowId] = useState<string | null>(null);

  const eligible =
    !isActor &&
    !isSpectator &&
    myReactionCards.length > 0 &&
    passedWindowId !== pending.window_id;

  const title = pendingCard?.title || "a card";

  if (!eligible) {
    return (
      <div className="fixed inset-x-0 bottom-4 z-50 mx-auto w-fit max-w-[calc(100%-2rem)] -rotate-[0.4deg] rounded-xl border-2 border-ink bg-white px-4 py-2.5 sticker-shadow-sm">
        <p className="font-hand text-base">
          ⏳ {actorName} plays <b>“{title}”</b> — waiting to see if anyone
          reacts…
        </p>
        <CountdownBar deadlineEpochMs={pending.deadline_epoch_ms} />
      </div>
    );
  }

  return (
    // Controlled `open` with no onOpenChange: the window can only be left via
    // React/Pass (or the server closing it), never by outside-click/Esc.
    <Dialog open>
      <DialogContent
        className="animate-popin border-2 border-dashed border-ink bg-panel-paper shadow-none"
        showCloseButton={false}
      >
        <DialogHeader>
          <DialogTitle className="font-hand text-xl font-normal">
            ⚡ {actorName} plays <b>“{title}”</b> — react?
          </DialogTitle>
          <DialogDescription className="font-hand text-base">
            Play a reaction card before it resolves, or pass.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-start gap-5">
          {pendingCard && (
            <div className="text-center">
              <SketchCard
                card={pendingCard}
                w={110}
                rot={-2}
                artUrl={getCardArtUrl(roomCode, pendingCard)}
              />
              <p className="mt-1 font-hand text-sm text-muted-foreground">
                the pending play
              </p>
            </div>
          )}
          <div className="flex flex-1 flex-col gap-2">
            <p className="font-hand text-sm uppercase tracking-wide text-muted-foreground">
              Your reactions
            </p>
            <div className="flex flex-wrap items-end gap-1">
              {myReactionCards.map((card) => (
                <SketchCard
                  key={card.id}
                  card={card}
                  w={104}
                  rot={selectedId === card.id ? 0 : 2}
                  selectable
                  selected={selectedId === card.id}
                  onClick={() => setSelectedId(card.id)}
                  artUrl={getCardArtUrl(roomCode, card)}
                  className={cn(
                    selectedId && selectedId !== card.id && "opacity-55",
                  )}
                />
              ))}
            </div>
          </div>
        </div>

        <CountdownBar deadlineEpochMs={pending.deadline_epoch_ms} />

        <div className="flex flex-wrap items-center gap-2">
          <Button
            disabled={!selectedId}
            onClick={() => {
              if (!selectedId) return;
              send({ type: "play", card_id: selectedId, as_reaction: true });
              setSelectedId(null);
            }}
          >
            ⚡ React
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              send({ type: "pass_reaction", window_id: pending.window_id });
              setPassedWindowId(pending.window_id);
              setSelectedId(null);
            }}
          >
            Pass
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
