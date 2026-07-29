import { Button } from "@/components/ui/button";
import { EffectLog } from "@/components/effect-log";
import { MEDALS, StandingRow } from "@/components/standing-row";
import { SketchCard, stableRotation } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import { playerColor } from "@/lib/players";
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
  onCorrectResults?: () => void;
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
  onCorrectResults,
  onBack,
}: ResultsScreenProps) {
  const isFinal = gameState.phase === "ended";

  let winnerIds = gameState.winner_ids;
  if (winnerIds === undefined && gameState.players.length > 0) {
    const top = Math.max(...gameState.players.map((p) => p.score));
    winnerIds = gameState.players
      .filter((p) => p.score === top)
      .map((p) => p.id);
  }
  winnerIds ??= [];

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

  // Standings sorted by score desc; identity colors stay keyed to the original
  // turn-order index so they match the table view.
  const standings = gameState.players
    .map((p, index) => ({ player: p, color: playerColor(index) }))
    .sort((a, b) => b.player.score - a.player.score);
  const maxScore = Math.max(1, ...standings.map((s) => s.player.score));

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col items-center gap-5 pt-4 pb-10">
      <h2
        className={cn(
          "text-center font-marker text-[40px] leading-[0.9]",
          iWon ? "text-primary" : "text-ink",
        )}
      >
        {headline}
      </h2>
      {winnerNames.length > 0 && (
        <p className="font-hand text-[19px] text-muted-foreground">
          {winnerNames.length > 1 ? "Winners" : "Winner"}:{" "}
          {winnerNames.join(", ")}
        </p>
      )}

      <div className="flex w-full flex-col gap-3.5">
        {standings.map(({ player, color }, rank) => (
          <StandingRow
            key={player.id}
            name={player.name}
            score={player.score}
            color={color}
            rank={rank}
            maxScore={maxScore}
            avatarSize={50}
            nameSuffix={`${player.id === myPlayerId ? " (you)" : ""} ${MEDALS[rank] ?? ""}`}
          />
        ))}
      </div>

      {gameState.epilogue_result && (
        <EpilogueOutcomeLists
          result={gameState.epilogue_result}
          cards={gameState.cards}
          roomCode={gameState.room_code}
        />
      )}
      {!isFinal && isHost && (
        <div className="grid w-full grid-cols-1 gap-2 sm:w-auto sm:grid-cols-2">
          <Button
            variant="outline"
            size="lg"
            className="font-marker text-lg"
            onClick={onCorrectResults}
          >
            Correct results
          </Button>
          <Button
            size="lg"
            className="font-marker text-lg"
            onClick={() => send({ type: "epilogue_start" })}
          >
            Start epilogue
          </Button>
        </div>
      )}
      {!isFinal && !isHost && (
        <p className="font-hand text-base italic text-muted-foreground">
          Waiting for the host to start the epilogue vote…
        </p>
      )}
      {isFinal && (
        <Button variant="outline" onClick={onBack}>
          Back to lobby
        </Button>
      )}
      <EffectLog
        log={log}
        brewing={null}
        className="w-full rounded-xl border-2"
      />
    </div>
  );
}

function EpilogueOutcomeLists({
  result,
  cards,
  roomCode,
}: {
  result: {
    kept: EpilogueCardOutcome[];
    destroyed: EpilogueCardOutcome[];
    favorite_card_ids?: string[];
  };
  cards: Record<string, CardSnapshot>;
  roomCode: string;
}) {
  // Defensive re-normalization of what the backend already guarantees: only
  // kept cards can be favorites, so an odd/old snapshot never decorates a
  // destroyed card.
  const keptIds = new Set(result.kept.map((o) => o.id));
  const favoriteIds = new Set(
    (result.favorite_card_ids ?? []).filter((id) => keptIds.has(id)),
  );
  const favoriteTitles = result.kept
    .filter((o) => favoriteIds.has(o.id))
    .map((o) => o.title || "Untitled card");

  return (
    <div className="flex w-full max-w-2xl flex-col gap-3">
      <p className="text-center font-hand text-[17px]">
        {favoriteTitles.length > 0 ? (
          <>
            <span aria-hidden="true" className="text-amber">
              ★
            </span>{" "}
            Table {favoriteTitles.length > 1 ? "favorites" : "favorite"} —{" "}
            <span className="sr-only">Most Keep votes this game: </span>
            <span className="font-marker text-base">
              {favoriteTitles.join(", ")}
            </span>
          </>
        ) : (
          "No table favorite this game."
        )}
      </p>
      <div className="grid w-full grid-cols-1 gap-4 sm:grid-cols-2">
        <OutcomeColumn
          title="Kept"
          outcomes={result.kept}
          cards={cards}
          roomCode={roomCode}
          favoriteIds={favoriteIds}
        />
        <OutcomeColumn
          title="Destroyed"
          outcomes={result.destroyed}
          cards={cards}
          roomCode={roomCode}
          destroyed
        />
      </div>
    </div>
  );
}

function OutcomeColumn({
  title,
  outcomes,
  cards,
  roomCode,
  destroyed,
  favoriteIds,
}: {
  title: string;
  outcomes: EpilogueCardOutcome[];
  cards: Record<string, CardSnapshot>;
  roomCode: string;
  destroyed?: boolean;
  favoriteIds?: Set<string>;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-2xl border-[2.5px] border-ink bg-card p-3 panel-shadow">
      <p
        className={cn(
          "font-marker text-sm",
          destroyed ? "text-primary" : "text-marker-green",
        )}
      >
        {title} ({outcomes.length})
      </p>
      {outcomes.length === 0 ? (
        <p className="font-hand text-sm italic text-muted-foreground">None.</p>
      ) : (
        <div className="flex flex-wrap gap-3 px-1 pb-2 pt-1">
          {outcomes.map((outcome) => {
            const card = cards[outcome.id];
            const isFavorite =
              !destroyed && Boolean(favoriteIds?.has(outcome.id));
            return (
              <div
                key={outcome.id}
                className="flex flex-col items-center gap-1"
              >
                <SketchCard
                  card={card}
                  title={card ? undefined : outcome.title}
                  w={92}
                  rot={stableRotation(outcome.id, 3)}
                  artUrl={card ? getCardArtUrl(roomCode, card) : null}
                  className={cn(
                    destroyed && "opacity-70 grayscale",
                    isFavorite &&
                      "rounded-md ring-[3px] ring-amber ring-offset-2 ring-offset-card",
                  )}
                />
                {isFavorite && (
                  <span className="rounded-full border border-amber bg-amber/15 px-2 py-0.5 font-hand text-[13px] leading-tight text-ink">
                    <span aria-hidden="true">★</span> Table favorite
                    <span className="sr-only">
                      {" "}
                      — Most Keep votes this game
                    </span>
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
