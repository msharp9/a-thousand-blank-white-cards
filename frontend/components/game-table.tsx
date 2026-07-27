"use client";

import { useDroppable } from "@dnd-kit/core";
import { PlayerAvatar } from "@/components/player-avatar";
import { getCardArtUrl } from "@/lib/art";
import { seatDropId } from "@/lib/dnd";
import {
  conditionDuration,
  conditionName,
  conditionValueDetail,
} from "@/lib/conditions";
import { playerColor } from "@/lib/players";
import {
  projectSeats,
  SEAT_EDGE_LABELS,
  seatEdge,
  type SeatEdge,
} from "@/lib/seating";
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
 * Compact player-facing label shared with the Status view's capitalization,
 * value details, and turn-based duration language.
 */
export function conditionLabel(
  key: string,
  value: unknown,
  ttl?: number,
): string {
  const detail = conditionValueDetail(value);
  const duration = conditionDuration(ttl);
  return [conditionName(key), detail, duration].filter(Boolean).join(" · ");
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
 *
 * Seats project viewer-relative from the mutable turn_order (lib/seating):
 * far-left is the seated viewer's turn-order successor, far-right their
 * predecessor; spectators see the canonical order. Identity colors stay keyed
 * to the roster index so they never shift when the turn order does.
 */
export function GameTable({ gameState, myPlayerId }: GameTableProps) {
  const { players, spectators, turn_index, turn_order, cards } = gameState;
  const activePlayerId = players.length
    ? players[turn_index % players.length]?.id
    : undefined;
  const rosterIndex = new Map(players.map((p, index) => [p.id, index]));
  const viewerSeated = rosterIndex.has(myPlayerId);
  const seats = projectSeats(
    turn_order,
    players.map((p) => p.id),
    myPlayerId,
  )
    .map((id) => players[rosterIndex.get(id) ?? -1])
    .filter((p): p is PlayerSnapshot => Boolean(p));

  return (
    <div className="flex min-w-0 flex-col gap-2 px-3 pt-3 pb-1.5 sm:px-5 sm:pt-5">
      <div
        data-opponent-rail
        className="-mx-3 overflow-x-auto overscroll-x-contain px-3 snap-x snap-mandatory sm:mx-0 sm:overflow-visible sm:px-0"
      >
        <div className="flex w-max justify-start gap-3 sm:w-auto sm:flex-wrap sm:justify-center sm:gap-6">
          {seats.map((player, seatIndex) => (
            <OpponentPanel
              key={player.id}
              player={player}
              color={playerColor(rosterIndex.get(player.id) ?? seatIndex)}
              edge={seatEdge(seatIndex, seats.length, viewerSeated)}
              cards={cards}
              roomCode={gameState.room_code}
              isActive={player.id === activePlayerId}
              myPlayerId={myPlayerId}
            />
          ))}
        </div>
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
  edge,
  cards,
  roomCode,
  isActive,
  myPlayerId,
}: {
  player: PlayerSnapshot;
  color: string;
  edge: SeatEdge | null;
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
      data-seat-edge={edge ?? undefined}
      role="group"
      aria-label={
        edge ? `${player.name} — ${SEAT_EDGE_LABELS[edge]}` : player.name
      }
      className={cn(
        "flex max-w-[280px] shrink-0 snap-center flex-col items-center gap-1.5 overflow-hidden rounded-[14px] bg-card/60 px-3 py-2 sm:max-w-none",
        "transition-[box-shadow,transform] duration-150",
        isOver && "scale-[1.03]",
        (!player.connected || player.eliminated) && "opacity-50",
      )}
      style={{
        border: `2px dashed ${color}`,
        boxShadow: isOver ? `0 0 0 3px ${color}, 0 0 16px ${color}` : undefined,
      }}
    >
      {edge && (
        <span
          data-seat-edge-label
          className="font-hand text-[11px] leading-none text-muted-foreground"
        >
          {SEAT_EDGE_LABELS[edge]}
        </span>
      )}
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
          {player.eliminated && (
            <span className="ml-1 text-[13px] text-muted-foreground">
              · eliminated
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
