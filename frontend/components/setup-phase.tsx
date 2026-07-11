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
  previewResult: {
    program?: string | null;
    snippet?: string | null;
    verdict: string;
  } | null;
  isHost?: boolean;
  isSpectator?: boolean;
}

const DEFAULT_TARGET_AUTHORED = 5;

export function SetupPhase({
  gameState,
  myPlayerId,
  send,
  previewResult,
  isHost,
  isSpectator,
}: SetupPhaseProps) {
  const [dialogOpen, setDialogOpen] = useState(false);

  // How many cards each player must author, from the backend (fall back to the
  // historical default if an older snapshot omits it).
  const target = gameState.cards_to_author ?? DEFAULT_TARGET_AUTHORED;

  const me = gameState.players.find((p) => p.id === myPlayerId);
  const myCards: CardSnapshot[] = (me?.hand ?? [])
    .map((id) => gameState.cards[id])
    .filter((c): c is CardSnapshot => Boolean(c));
  const myAuthored = Object.values(gameState.cards).filter(
    (c) => c.creator_id === myPlayerId || c.author_id === myPlayerId,
  );
  // Prefer the backend's authoritative per-player count; fall back to the local
  // tally of authored cards if setup_progress is missing.
  const myAuthoredCount =
    gameState.setup_progress?.[myPlayerId] ?? myAuthored.length;
  const remaining = Math.max(0, target - myAuthoredCount);

  // The 30 pre-made pool cards: during setup the backend parks the premade pool
  // in state.deck, so resolving those ids gives exactly the pool to show for
  // authoring-with-synergy.
  const premadeCards: CardSnapshot[] = gameState.deck
    .map((id) => gameState.cards[id])
    .filter((c): c is CardSnapshot => Boolean(c));

  // A spectator joined after the game started (setup counts as started): they
  // cannot author cards, so show a watch-only notice instead of the setup UI.
  if (isSpectator) {
    return (
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        <h2 className="text-xl font-bold">Setup in progress</h2>
        <p className="text-sm italic text-muted-foreground">
          You joined after the game started — you are spectating and cannot
          author cards.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <div>
        <h2 className="text-xl font-bold">Setup — Author your cards</h2>
        <p className="text-sm text-muted-foreground">
          Write {target} cards to seed the deck. {remaining} to go.
        </p>
        {gameState.setup_progress && gameState.players.length > 0 && (
          <p className="mt-1 text-xs text-muted-foreground">
            {gameState.players
              .filter((p) => !p.spectator)
              .map(
                (p) =>
                  `${p.name} ${gameState.setup_progress[p.id] ?? 0}/${target}`,
              )
              .join(", ")}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">
            Cards you authored ({myAuthored.length})
          </p>
          <Button
            size="sm"
            onClick={() => setDialogOpen(true)}
            disabled={remaining === 0}
          >
            {remaining === 0 ? "All cards authored" : "Author a card"}
          </Button>
        </div>
        {myAuthored.length === 0 ? (
          <p className="text-xs italic text-muted-foreground">
            No cards yet — click “Author a card”.
          </p>
        ) : (
          <ScrollArea className="max-h-[28rem] w-full">
            <div className="grid grid-cols-3 gap-2 pb-2 sm:grid-cols-4 md:grid-cols-5">
              {myAuthored.map((card) => (
                <CardTile key={card.id} card={card} className="w-full" />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>

      {myCards.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">
            Your dealt hand ({myCards.length})
          </p>
          <ScrollArea className="max-h-[28rem] w-full">
            <div className="grid grid-cols-3 gap-2 pb-2 sm:grid-cols-4 md:grid-cols-5">
              {myCards.map((card) => (
                <CardTile key={card.id} card={card} className="w-full" />
              ))}
            </div>
          </ScrollArea>
        </div>
      )}

      {premadeCards.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">
            Pre-made cards in the deck ({premadeCards.length})
          </p>
          <p className="text-xs text-muted-foreground">
            These ship with the deck — author cards that play well with them.
          </p>
          <ScrollArea className="max-h-[28rem] w-full">
            <div className="grid grid-cols-3 gap-2 pb-2 sm:grid-cols-4 md:grid-cols-5">
              {premadeCards.map((card) => (
                <CardTile key={card.id} card={card} className="w-full" />
              ))}
            </div>
          </ScrollArea>
        </div>
      )}

      {isHost && (
        <Button
          onClick={() => send({ type: "start" })}
          disabled={remaining > 0}
        >
          Start game
        </Button>
      )}

      <CreateCardDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        send={send}
        previewResult={previewResult}
      />
    </div>
  );
}
