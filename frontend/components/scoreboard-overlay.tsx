"use client";

import { OverlayShell } from "@/components/overlay-shell";
import { MEDALS, StandingRow } from "@/components/standing-row";
import { playerColor } from "@/lib/players";
import type { PlayerSnapshot } from "@/lib/types";

interface ScoreboardOverlayProps {
  players: PlayerSnapshot[];
  onClose: () => void;
}

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
    <OverlayShell
      scrimTestId="scoreboard-scrim"
      title="Scoreboard"
      subtitle="How everyone’s doing right now"
      closeLabel="Close scoreboard"
      onClose={onClose}
      panelClassName="max-w-[720px]"
    >
      <div className="flex flex-col gap-3.5">
        {standings.map(({ player, color }, rank) => (
          <StandingRow
            key={player.id}
            name={player.name}
            score={player.score}
            color={color}
            rank={rank}
            maxScore={maxScore}
            avatarSize={46}
            nameSuffix={` ${MEDALS[rank] ?? ""}`}
            caption={
              <p className="font-hand text-sm text-muted-foreground">
                {player.hand.length} in hand · {player.in_play.length} in play
              </p>
            }
          />
        ))}
      </div>
    </OverlayShell>
  );
}
