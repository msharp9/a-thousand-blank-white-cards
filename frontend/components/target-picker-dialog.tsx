"use client";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CardChoiceGrid } from "@/components/card-choice-grid";
import { playerColor } from "@/lib/players";
import type { CardSnapshot, PromptChoiceMsg } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The target picker shown when the server asks the active player to choose a
 * target for the card they just played. Player targets stay avatar-colored
 * buttons; card targets render as full card faces (snapshots carried on the
 * prompt cover hidden-hand candidates the shared state omits). Picking sends
 * a follow-up play carrying only the chosen id; cancelling abandons the
 * pending play (the turn never advanced).
 */
export function TargetPickerDialog({
  prompt,
  playedTitle,
  players,
  cards,
  roomCode,
  onPick,
  onCancel,
}: {
  prompt: PromptChoiceMsg | null;
  playedTitle: string;
  players: { id: string }[];
  cards: Record<string, CardSnapshot>;
  roomCode: string;
  onPick: (choice: PromptChoiceMsg["choices"][number]) => void;
  onCancel: () => void;
}) {
  const faces = { ...cards, ...(prompt?.cards ?? {}) };
  const playerChoices = (prompt?.choices ?? []).filter(
    (choice) => choice.player_id,
  );
  const cardChoices = (prompt?.choices ?? []).filter(
    (choice) => !choice.player_id && choice.card_id,
  );
  const cardNames = Object.fromEntries(
    cardChoices.map((choice) => [choice.card_id as string, choice.name]),
  );

  return (
    <Dialog
      open={Boolean(prompt)}
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
    >
      <DialogContent
        className={cn(
          "max-h-[90dvh] animate-popin overflow-y-auto overscroll-contain border-2 border-dashed border-ink bg-panel-paper shadow-none",
          cardChoices.length > 0 && "sm:max-w-2xl",
        )}
      >
        <DialogHeader>
          <DialogTitle className="font-hand text-xl font-normal">
            {playedTitle ? (
              <>
                Play <b>“{playedTitle}”</b> to:
              </>
            ) : (
              "Choose a target"
            )}
          </DialogTitle>
          {prompt && (
            <DialogDescription className="font-hand text-base">
              {prompt.prompt}
            </DialogDescription>
          )}
        </DialogHeader>
        {playerChoices.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            {playerChoices.map((choice) => {
              const targetIndex = players.findIndex(
                (p) => p.id === choice.player_id,
              );
              return (
                <Button
                  key={choice.player_id}
                  variant={targetIndex >= 0 ? "default" : "outline"}
                  style={
                    targetIndex >= 0
                      ? { backgroundColor: playerColor(targetIndex) }
                      : undefined
                  }
                  onClick={() => onPick(choice)}
                >
                  {choice.name}
                </Button>
              );
            })}
          </div>
        )}
        {cardChoices.length > 0 && (
          <CardChoiceGrid
            cardIds={cardChoices.map((choice) => choice.card_id as string)}
            faces={faces}
            roomCode={roomCode}
            names={cardNames}
            onChoose={(cardId) => {
              const choice = cardChoices.find((c) => c.card_id === cardId);
              if (choice) onPick(choice);
            }}
          />
        )}
        <div className="flex justify-end">
          <Button
            variant="ghost"
            className="text-muted-foreground"
            onClick={onCancel}
          >
            Cancel
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
