import { Badge } from "@/components/ui/badge";
import type {
  CardSnapshot,
  GameStateSnapshot,
  PlayerSnapshot,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface GameTableProps {
  gameState: GameStateSnapshot;
  myPlayerId: string;
}

export function GameTable({ gameState, myPlayerId }: GameTableProps) {
  const { players, spectators, turn_index, direction, deck, cards } = gameState;
  const activePlayer = players.length
    ? players[turn_index % players.length]
    : undefined;
  const directionLabel =
    direction === 1 ? "→ clockwise" : "← counter-clockwise";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span>Turn order:</span>
        <span className="font-mono">{directionLabel}</span>
        <span className="ml-auto tabular-nums" title="Cards left in the deck">
          Deck: {deck.length}
        </span>
      </div>
      <div className="flex flex-wrap gap-3">
        {players.map((player) => (
          <PlayerTile
            key={player.id}
            player={player}
            cards={cards}
            isActive={player.id === activePlayer?.id}
            isMe={player.id === myPlayerId}
          />
        ))}
      </div>
      {spectators.length > 0 && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>Spectating:</span>
          {spectators.map((s) => (
            <Badge key={s.id} variant="outline" className="text-[10px]">
              {s.name}
              {s.id === myPlayerId && " (you)"}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

function PlayerTile({
  player,
  cards,
  isActive,
  isMe,
}: {
  player: PlayerSnapshot;
  cards: Record<string, CardSnapshot>;
  isActive: boolean;
  isMe: boolean;
}) {
  // Cards this player has played in front of them, resolved to snapshots so
  // everyone at the table can see what others played.
  const inPlayCards = (player.in_play ?? [])
    .map((id) => cards[id])
    .filter((c): c is CardSnapshot => Boolean(c));

  return (
    <div
      className={cn(
        "flex flex-col items-center gap-1 rounded-lg border p-3 min-w-[110px]",
        isActive && "border-primary ring-2 ring-primary/30",
        !player.connected && "opacity-50",
      )}
    >
      <div className="flex items-center gap-1">
        <span className="text-sm font-semibold">{player.name}</span>
        {isMe && (
          <Badge variant="secondary" className="text-[10px]">
            you
          </Badge>
        )}
      </div>
      <span className="text-2xl font-bold tabular-nums">{player.score}</span>
      <span className="text-[10px] text-muted-foreground">
        {player.hand.length} cards
      </span>
      {isActive && <Badge className="text-[10px]">active</Badge>}
      {!player.connected && (
        <span className="text-[10px] text-muted-foreground">offline</span>
      )}
      {inPlayCards.length > 0 && (
        <div className="mt-1 flex w-full flex-col items-center gap-1">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            In play
          </span>
          <div className="flex flex-wrap justify-center gap-1">
            {inPlayCards.map((card) => (
              <span
                key={card.id}
                className="max-w-[100px] truncate rounded border bg-muted/40 px-1.5 py-0.5 text-[10px]"
                title={card.description || card.title}
              >
                {card.title || "Untitled"}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
