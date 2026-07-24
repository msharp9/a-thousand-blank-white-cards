"use client";

import { useDroppable } from "@dnd-kit/core";
import { PlayerAvatar } from "@/components/player-avatar";
import { getCardArtUrl } from "@/lib/art";
import { seatDropId } from "@/lib/dnd";
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

// Reserved keys the engine's turn loop consumes get friendly labels; any other
// key is a card-invented status, shown with underscores humanized.
const RESERVED_CONDITION_LABELS: Record<string, string> = {
  skip_next: "skips next turn",
  extra_turn: "extra turn",
};

/**
 * Player-facing label for one condition: friendly names for reserved keys,
 * "poisoned ×3" for numeric stacks, and ", 2 turns left" when the condition
 * carries a TTL (condition_ttls). A TTL of 0 means the current owner turn is
 * the condition's last active one, rendered as ", last turn".
 */
export function conditionLabel(
  key: string,
  value: unknown,
  ttl?: number,
): string {
  let label = RESERVED_CONDITION_LABELS[key] ?? key.replace(/_/g, " ");
  if (typeof value === "number") label += ` ×${value}`;
  if (ttl === 0) label += ", last turn";
  else if (ttl != null) label += `, ${ttl} turn${ttl === 1 ? "" : "s"} left`;
  return label;
}

function ConditionBadges({ player }: { player: PlayerSnapshot }) {
  // A falsy value means the condition is toggled off, not an active status.
  const active = Object.entries(player.conditions ?? {}).filter(([, value]) =>
    Boolean(value),
  );
  if (active.length === 0) return null;
  return (
    <div className="flex max-w-[240px] flex-wrap justify-center gap-1">
      {active.map(([key, value]) => {
        const label = conditionLabel(key, value, player.condition_ttls?.[key]);
        return (
          <span
            key={key}
            title={label}
            className="max-w-[180px] truncate rounded-lg border-[1.5px] border-ink/50 bg-panel-paper px-1.5 py-0.5 font-hand text-[11px] leading-tight text-ink/80"
          >
            {label}
          </span>
        );
      })}
    </div>
  );
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
              myPlayerId={myPlayerId}
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
  myPlayerId,
}: {
  player: PlayerSnapshot;
  color: string;
  cards: Record<string, CardSnapshot>;
  roomCode: string;
  isActive: boolean;
  myPlayerId: string;
}) {
  // Cards this player has played in front of them, resolved to snapshots so
  // everyone at the table can see what others played.
  const inPlayCards = (player.in_play ?? [])
    .map((id) => cards[id])
    .filter((c): c is CardSnapshot => Boolean(c));

  // The server redacts other players' hands to a bare count; the face-down
  // fan renders from that count (hand.length covers older servers).
  const handCount = player.hand_count ?? player.hand.length;

  // Revealed hand (reveal_hand op): face-up play (hand_public) or a hand
  // persistently revealed to ME. The server only sends real hand ids to
  // permitted viewers, so resolving against the registry is the content gate.
  const handRevealed =
    Boolean(player.hand_public) ||
    (player.hand_revealed_to ?? []).includes(myPlayerId);
  const revealedHandCards = handRevealed
    ? player.hand
        .map((id) => cards[id])
        .filter((c): c is CardSnapshot => Boolean(c))
    : [];

  // Drop target for a targeted play: dropping a dragged hand card on this
  // seat plays it with chosen_player_id = this player (see PlayDndContext).
  const { setNodeRef, isOver } = useDroppable({
    id: seatDropId(player.id),
    data: { type: "seat", playerId: player.id },
  });

  return (
    <div
      ref={setNodeRef}
      data-seat-drop={player.id}
      className={cn(
        "flex flex-col items-center gap-1.5 rounded-[14px] bg-card/60 px-3 py-2",
        "transition-[box-shadow,transform] duration-150",
        isOver && "scale-[1.03]",
        !player.connected && "opacity-50",
      )}
      style={{
        border: `2px dashed ${color}`,
        boxShadow: isOver ? `0 0 0 3px ${color}, 0 0 16px ${color}` : undefined,
      }}
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
        {handRevealed && (
          <span
            className="rounded-lg border-[1.5px] border-ink bg-panel-paper px-1.5 py-0.5 font-hand text-xs"
            title={
              player.hand_public
                ? "This hand is played face up"
                : "This hand is revealed to you"
            }
          >
            👀 revealed
          </span>
        )}
      </div>
      <ConditionBadges player={player} />
      {handCount > 0 &&
        (handRevealed && revealedHandCards.length > 0 ? (
          <div
            className="flex items-end"
            title={`${handCount} cards in hand (revealed)`}
          >
            {revealedHandCards.map((card, i) => (
              <SketchCard
                key={card.id}
                card={card}
                w={52}
                showTape={false}
                rot={(i - (revealedHandCards.length - 1) / 2) * 4}
                artUrl={getCardArtUrl(roomCode, card)}
                className={cn(i > 0 && "-ml-[16px]", "hover:z-10")}
              />
            ))}
          </div>
        ) : (
          <div className="flex items-end" title={`${handCount} cards in hand`}>
            {Array.from({ length: handCount }, (_, i) => (
              <SketchCard
                key={i}
                w={40}
                faceDown
                showTape={false}
                rot={(i - (handCount - 1) / 2) * 5}
                className={cn(i > 0 && "-ml-[22px]")}
              />
            ))}
          </div>
        ))}
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
