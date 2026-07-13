"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { getCardArtUrl } from "@/lib/art";
import type {
  CardSnapshot,
  ClientMsg,
  GameStateSnapshot,
  PreviewResult,
} from "@/lib/types";
import { CreateCardDialog } from "./create-card-dialog";
import { SketchCard } from "./sketch-card";

interface SetupPhaseProps {
  gameState: GameStateSnapshot;
  myPlayerId: string;
  send: (msg: ClientMsg) => void;
  previewResult: PreviewResult | null;
  isSpectator?: boolean;
}

const DEFAULT_TARGET_AUTHORED = 5;

export function SetupPhase({
  gameState,
  myPlayerId,
  send,
  previewResult,
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
        <h2 className="font-marker text-2xl leading-[0.95]">
          Setup in progress
        </h2>
        <p className="font-hand text-base italic text-muted-foreground">
          You joined after the game started — you are spectating and cannot
          author cards.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <div>
        <h2 className="font-marker text-[28px] leading-[0.95]">
          Setup — Author your cards
        </h2>
        <p className="mt-1 font-hand text-lg text-muted-foreground">
          Write {target} cards to seed the deck. {remaining} to go.
        </p>
        {gameState.setup_progress && gameState.players.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {gameState.players.map((p) => (
              <span
                key={p.id}
                className="rounded-[10px] border-[1.5px] border-ink bg-white px-2.5 py-0.5 font-hand text-sm"
              >
                {p.name} {gameState.setup_progress[p.id] ?? 0}/{target}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <p className="font-hand text-lg">
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
          <p className="font-hand text-sm italic text-muted-foreground">
            No cards yet — click “Author a card”.
          </p>
        ) : (
          <ScrollArea className="max-h-[28rem] w-full">
            <div className="flex flex-wrap gap-3 px-1 pb-3 pt-2">
              {myAuthored.map((card) => (
                <SketchCard
                  key={card.id}
                  card={card}
                  w={130}
                  artUrl={getCardArtUrl(gameState.room_code, card)}
                />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>

      {myCards.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="font-hand text-lg">
            Your dealt hand ({myCards.length})
          </p>
          <ScrollArea className="max-h-[28rem] w-full">
            <div className="flex flex-wrap gap-3 px-1 pb-3 pt-2">
              {myCards.map((card) => (
                <SketchCard
                  key={card.id}
                  card={card}
                  w={130}
                  artUrl={getCardArtUrl(gameState.room_code, card)}
                />
              ))}
            </div>
          </ScrollArea>
        </div>
      )}

      {premadeCards.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="font-hand text-lg">
            Pre-made cards in the deck ({premadeCards.length})
          </p>
          <p className="font-hand text-sm text-muted-foreground">
            These ship with the deck — author cards that play well with them.
          </p>
          <ScrollArea className="max-h-[28rem] w-full">
            <div className="flex flex-wrap gap-3 px-1 pb-3 pt-2">
              {premadeCards.map((card) => (
                <SketchCard
                  key={card.id}
                  card={card}
                  w={92}
                  artUrl={getCardArtUrl(gameState.room_code, card)}
                />
              ))}
            </div>
          </ScrollArea>
        </div>
      )}

      <p className="font-hand text-sm italic text-muted-foreground">
        The game starts automatically once everyone has authored their cards.
      </p>

      <CreateCardDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        send={send}
        previewResult={previewResult}
      />
    </div>
  );
}
