import { Button } from "@/components/ui/button";
import { EffectLog } from "@/components/effect-log";
import { GameTable } from "@/components/game-table";
import { SketchCard, stableRotation } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import type {
  CardSnapshot,
  ClientMsg,
  EpilogueCardOutcome,
  GameStateSnapshot,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface ResultsScreenProps {
  gameState: GameStateSnapshot;
  myPlayerId: string;
  log: string[];
  isHost: boolean;
  send: (msg: ClientMsg) => void;
  onBack: () => void;
}

// Shared end-of-game screen for both stops in the results-first flow:
// - phase "results": scores + full history, host advances into the epilogue
//   vote via "Start epilogue".
// - phase "ended": the same scores + history, plus the epilogue's
//   kept/destroyed outcome lists (gameState.epilogue_result) and a "Back to
//   lobby" exit.
//
// Winners come from the backend's authoritative winner_ids (mirrors
// GameState.winner_ids, populated as soon as phase reaches "results"); we
// fall back to computing the highest score client-side only if the field is
// somehow absent, so an older snapshot still renders a sensible result.
export function ResultsScreen({
  gameState,
  myPlayerId,
  log,
  isHost,
  send,
  onBack,
}: ResultsScreenProps) {
  const isFinal = gameState.phase === "ended";

  let winnerIds = gameState.winner_ids ?? [];
  if (winnerIds.length === 0 && gameState.players.length > 0) {
    const top = Math.max(...gameState.players.map((p) => p.score));
    winnerIds = gameState.players
      .filter((p) => p.score === top)
      .map((p) => p.id);
  }

  const iWon = winnerIds.includes(myPlayerId);
  const winnerNames = gameState.players
    .filter((p) => winnerIds.includes(p.id))
    .map((p) => p.name);

  let headline: string;
  if (winnerIds.length === 0) {
    headline = "Game over";
  } else if (iWon) {
    headline = winnerIds.length > 1 ? "You tied for the win!" : "You win! 🎉";
  } else {
    headline = "You lose";
  }

  return (
    <div className="flex flex-col items-center gap-4">
      <h2
        className={
          iWon
            ? "text-2xl font-bold text-primary"
            : "text-2xl font-bold text-muted-foreground"
        }
      >
        {headline}
      </h2>
      {winnerNames.length > 0 && (
        <p className="text-sm text-muted-foreground">
          {winnerNames.length > 1 ? "Winners" : "Winner"}:{" "}
          {winnerNames.join(", ")}
        </p>
      )}
      <GameTable gameState={gameState} myPlayerId={myPlayerId} />
      <EffectLog log={log} brewing={null} className="w-full max-w-2xl" />
      {gameState.epilogue_result && (
        <EpilogueOutcomeLists
          result={gameState.epilogue_result}
          cards={gameState.cards}
          roomCode={gameState.room_code}
        />
      )}
      {!isFinal && isHost && (
        <Button onClick={() => send({ type: "epilogue_start" })}>
          Start epilogue
        </Button>
      )}
      {!isFinal && !isHost && (
        <p className="text-sm italic text-muted-foreground">
          Waiting for the host to start the epilogue vote…
        </p>
      )}
      {isFinal && (
        <Button variant="outline" onClick={onBack}>
          Back to lobby
        </Button>
      )}
    </div>
  );
}

function EpilogueOutcomeLists({
  result,
  cards,
  roomCode,
}: {
  result: { kept: EpilogueCardOutcome[]; destroyed: EpilogueCardOutcome[] };
  cards: Record<string, CardSnapshot>;
  roomCode: string;
}) {
  return (
    <div className="grid w-full max-w-2xl grid-cols-1 gap-4 sm:grid-cols-2">
      <OutcomeColumn
        title="Kept"
        outcomes={result.kept}
        cards={cards}
        roomCode={roomCode}
      />
      <OutcomeColumn
        title="Destroyed"
        outcomes={result.destroyed}
        cards={cards}
        roomCode={roomCode}
        destroyed
      />
    </div>
  );
}

function OutcomeColumn({
  title,
  outcomes,
  cards,
  roomCode,
  destroyed,
}: {
  title: string;
  outcomes: EpilogueCardOutcome[];
  cards: Record<string, CardSnapshot>;
  roomCode: string;
  destroyed?: boolean;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3">
      <p className="font-hand text-sm uppercase tracking-wide text-muted-foreground">
        {title} ({outcomes.length})
      </p>
      {outcomes.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">None.</p>
      ) : (
        <div className="flex flex-wrap gap-3 px-1 pb-2 pt-1">
          {outcomes.map((outcome) => {
            const card = cards[outcome.id];
            return (
              <SketchCard
                key={outcome.id}
                card={card}
                title={card ? undefined : outcome.title}
                w={92}
                rot={stableRotation(outcome.id, 3)}
                artUrl={card ? getCardArtUrl(roomCode, card) : null}
                className={cn(destroyed && "opacity-70 grayscale")}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
