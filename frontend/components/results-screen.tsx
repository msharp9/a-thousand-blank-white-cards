import { Button } from "@/components/ui/button";
import { EffectLog } from "@/components/effect-log";
import { PlayerAvatar } from "@/components/player-avatar";
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
  onBack: () => void;
}

const MEDALS = ["🥇", "🥈", "🥉"];

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
          <div
            key={player.id}
            className={cn(
              "flex items-center gap-4 rounded-2xl border-[2.5px] border-ink bg-white px-4 py-3.5 panel-shadow",
              rank % 2 ? "rotate-[0.5deg]" : "-rotate-[0.5deg]",
            )}
          >
            <span
              className="w-11 shrink-0 text-center font-marker text-3xl"
              style={{ color: rank === 0 ? "var(--color-amber)" : "#999" }}
            >
              #{rank + 1}
            </span>
            <PlayerAvatar name={player.name} color={color} size={50} />
            <div className="min-w-0 flex-1">
              <p className="truncate font-hand text-2xl leading-[0.95]">
                {player.name}
                {player.id === myPlayerId && " (you)"} {MEDALS[rank] ?? ""}
              </p>
              <div className="mt-1.5 h-2.5 overflow-hidden rounded-full border-[1.5px] border-ink bg-[#eee]">
                <div
                  className="h-full"
                  style={{
                    width: `${Math.round(
                      (Math.max(0, player.score) / maxScore) * 100,
                    )}%`,
                    background: color,
                  }}
                />
              </div>
            </div>
            <span
              className="shrink-0 font-marker text-[34px] tabular-nums"
              style={{ color }}
            >
              {player.score}
            </span>
          </div>
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
        <Button
          size="lg"
          className="font-marker text-lg"
          onClick={() => send({ type: "epilogue_start" })}
        >
          Start epilogue
        </Button>
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
    <div className="flex flex-col gap-2 rounded-2xl border-[2.5px] border-ink bg-white p-3 panel-shadow">
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
