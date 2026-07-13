import { PlayerAvatar } from "@/components/player-avatar";
import { getCardArtUrl } from "@/lib/art";
import { playerColor } from "@/lib/players";
import type {
  CardSnapshot,
  GameStateSnapshot,
  PlayerSnapshot,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { SketchCard, stableRotation } from "./sketch-card";

interface GameTableProps {
  gameState: GameStateSnapshot;
  myPlayerId: string;
}

/**
 * The opponents row at the top of the Play Table: one panel per non-self
 * player (all players when spectating), dashed-bordered in that player's
 * identity color, with their face-down hand fan and in-front cards.
 */
export function GameTable({ gameState, myPlayerId }: GameTableProps) {
  const { players, spectators, turn_index, cards } = gameState;
  const activePlayerId = players.length
    ? players[turn_index % players.length]?.id
    : undefined;

  return (
    <div className="flex flex-col gap-2 px-5 pt-5 pb-1.5">
      <div className="flex flex-wrap justify-center gap-6">
        {players.map((player, index) =>
          player.id === myPlayerId ? null : (
            <OpponentPanel
              key={player.id}
              player={player}
              color={playerColor(index)}
              cards={cards}
              roomCode={gameState.room_code}
              isActive={player.id === activePlayerId}
            />
          ),
        )}
      </div>
      {spectators.length > 0 && (
        <div className="flex flex-wrap items-center justify-center gap-2 font-hand text-sm text-muted-foreground">
          <span>Spectating:</span>
          {spectators.map((s) => (
            <span
              key={s.id}
              className="rounded-lg border-[1.5px] border-ink bg-card px-2 py-0.5 text-ink"
            >
              {s.name}
              {s.id === myPlayerId && " (you)"}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function OpponentPanel({
  player,
  color,
  cards,
  roomCode,
  isActive,
}: {
  player: PlayerSnapshot;
  color: string;
  cards: Record<string, CardSnapshot>;
  roomCode: string;
  isActive: boolean;
}) {
  // Cards this player has played in front of them, resolved to snapshots so
  // everyone at the table can see what others played.
  const inPlayCards = (player.in_play ?? [])
    .map((id) => cards[id])
    .filter((c): c is CardSnapshot => Boolean(c));

  return (
    <div
      className={cn(
        "flex flex-col items-center gap-1.5 rounded-[14px] bg-card/60 px-3 py-2",
        !player.connected && "opacity-50",
      )}
      style={{ border: `2px dashed ${color}` }}
    >
      <div className="flex items-center gap-2">
        <PlayerAvatar name={player.name} color={color} size={34} />
        <span className="font-hand text-[19px] leading-none">
          {player.name}
          {isActive && (
            <span className="ml-1 text-[15px] text-primary">· playing</span>
          )}
          {!player.connected && (
            <span className="ml-1 text-[13px] text-muted-foreground">
              · offline
            </span>
          )}
        </span>
        <span className="font-marker text-lg tabular-nums" style={{ color }}>
          {player.score}
        </span>
      </div>
      {player.hand.length > 0 && (
        <div
          className="flex items-end"
          title={`${player.hand.length} cards in hand`}
        >
          {player.hand.map((id, i) => (
            <SketchCard
              key={id}
              w={40}
              faceDown
              showTape={false}
              rot={(i - (player.hand.length - 1) / 2) * 5}
              className={cn(i > 0 && "-ml-[22px]")}
            />
          ))}
        </div>
      )}
      {inPlayCards.length > 0 && (
        <div className="mt-0.5 flex items-center gap-1.5 border-t-[1.5px] border-dashed border-ink/20 pt-1.5">
          <span className="whitespace-nowrap font-hand text-xs text-muted-foreground">
            in front:
          </span>
          {inPlayCards.map((card) => (
            <SketchCard
              key={card.id}
              card={card}
              w={52}
              showTape={false}
              rot={stableRotation(card.id, 4)}
              artUrl={getCardArtUrl(roomCode, card)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
