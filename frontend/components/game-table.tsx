import { Badge } from "@/components/ui/badge";
import type { GameStateSnapshot, PlayerSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

interface GameTableProps {
  gameState: GameStateSnapshot;
  myPlayerId: string;
}

export function GameTable({ gameState, myPlayerId }: GameTableProps) {
  const { players, turn_index, direction } = gameState;
  const activePlayer = players.length ? players[turn_index % players.length] : undefined;
  const directionLabel = direction === 1 ? "→ clockwise" : "← counter-clockwise";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>Turn order:</span>
        <span className="font-mono">{directionLabel}</span>
      </div>
      <div className="flex flex-wrap gap-3">
        {players.map((player) => (
          <PlayerTile
            key={player.id}
            player={player}
            isActive={player.id === activePlayer?.id}
            isMe={player.id === myPlayerId}
          />
        ))}
      </div>
    </div>
  );
}

function PlayerTile({ player, isActive, isMe }: { player: PlayerSnapshot; isActive: boolean; isMe: boolean }) {
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
        {isMe && <Badge variant="secondary" className="text-[10px]">you</Badge>}
      </div>
      <span className="text-2xl font-bold tabular-nums">{player.score}</span>
      <span className="text-[10px] text-muted-foreground">{player.hand.length} cards</span>
      {isActive && <Badge className="text-[10px]">active</Badge>}
      {!player.connected && <span className="text-[10px] text-muted-foreground">offline</span>}
    </div>
  );
}
