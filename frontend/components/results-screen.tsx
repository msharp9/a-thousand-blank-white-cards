import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EffectLog } from "@/components/effect-log";
import { GameTable } from "@/components/game-table";
import type {
  ClientMsg,
  EpilogueCardOutcome,
  GameStateSnapshot,
} from "@/lib/types";

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
        <EpilogueOutcomeLists result={gameState.epilogue_result} />
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
}: {
  result: { kept: EpilogueCardOutcome[]; destroyed: EpilogueCardOutcome[] };
}) {
  return (
    <div className="grid w-full max-w-2xl grid-cols-1 gap-4 sm:grid-cols-2">
      <OutcomeColumn title="Kept" variant="default" cards={result.kept} />
      <OutcomeColumn
        title="Destroyed"
        variant="destructive"
        cards={result.destroyed}
      />
    </div>
  );
}

function OutcomeColumn({
  title,
  variant,
  cards,
}: {
  title: string;
  variant: "default" | "destructive";
  cards: EpilogueCardOutcome[];
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title} ({cards.length})
      </p>
      {cards.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">None.</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {cards.map((card) => (
            <Badge key={card.id} variant={variant} className="text-[10px]">
              {card.title || "Untitled"}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
