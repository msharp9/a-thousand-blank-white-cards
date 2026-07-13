"use client";

import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PlayerAvatar } from "@/components/player-avatar";
import { playerColor } from "@/lib/players";
import type { PlayerSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ScoreboardOverlayProps {
  players: PlayerSnapshot[];
  onClose: () => void;
}

const MEDALS = ["🥇", "🥈", "🥉"];

/**
 * Full-screen Scoreboard overlay (design's Scores tab): one row per player,
 * sorted by score desc, in their table identity color. Unlike the end-game
 * ResultsScreen (whose hands are empty by then), this mid-game view also
 * surfaces live hand/in-play counts — read straight off the snapshot each
 * render, so it never goes stale while the WS keeps pushing state.
 */
export function ScoreboardOverlay({
  players,
  onClose,
}: ScoreboardOverlayProps) {
  const standings = players
    .map((player, index) => ({ player, color: playerColor(index) }))
    .sort((a, b) => b.player.score - a.player.score);
  const maxScore = Math.max(1, ...standings.map((s) => s.player.score));

  return (
    <div
      data-testid="scoreboard-scrim"
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-[rgba(20,18,14,0.55)] p-4"
      onClick={onClose}
    >
      <div
        className="flex w-full max-w-[720px] flex-col overflow-hidden rounded-[18px] border-[3px] border-ink bg-card shadow-[8px_8px_0_rgba(26,26,26,0.8)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between border-b-2 border-ink px-5 py-3.5">
          <div>
            <h2 className="font-marker text-2xl">Scoreboard</h2>
            <p className="font-hand text-[15px] text-muted-foreground">
              How everyone&rsquo;s doing right now
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onClose}
            aria-label="Close scoreboard"
          >
            <XIcon />
          </Button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-5">
          <div className="flex flex-col gap-3.5">
            {standings.map(({ player, color }, rank) => (
              <div
                key={player.id}
                className={cn(
                  "flex items-center gap-4 rounded-2xl border-[2.5px] border-ink bg-card px-4 py-3.5 panel-shadow",
                  rank % 2 ? "rotate-[0.5deg]" : "-rotate-[0.5deg]",
                )}
              >
                <span
                  className="w-11 shrink-0 text-center font-marker text-3xl"
                  style={{
                    color:
                      rank === 0
                        ? "var(--color-amber)"
                        : "var(--muted-foreground)",
                  }}
                >
                  #{rank + 1}
                </span>
                <PlayerAvatar name={player.name} color={color} size={46} />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-hand text-2xl leading-[0.95]">
                    {player.name} {MEDALS[rank] ?? ""}
                  </p>
                  <p className="font-hand text-sm text-muted-foreground">
                    {player.hand.length} in hand · {player.in_play.length} in
                    play
                  </p>
                  <div className="mt-1.5 h-2.5 overflow-hidden rounded-full border-[1.5px] border-ink bg-muted">
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
        </div>
      </div>
    </div>
  );
}
