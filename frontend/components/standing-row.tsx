import type { ReactNode } from "react";
import { PlayerAvatar } from "@/components/player-avatar";
import { cn } from "@/lib/utils";

export const MEDALS = ["🥇", "🥈", "🥉"];

interface StandingRowProps {
  name: string;
  score: number;
  color: string;
  rank: number;
  maxScore: number;
  avatarSize: number;
  // Extra text after the name (e.g. medal, "(you)") and an optional caption
  // line under it (e.g. hand/in-play counts). Kept as slots so the two
  // callers keep their own copy without the row hand-rolling medals + bar math.
  nameSuffix?: ReactNode;
  caption?: ReactNode;
}

/**
 * One standings row shared by the mid-game ScoreboardOverlay and the end-game
 * ResultsScreen: rank badge, identity-colored avatar, name, a score bar scaled
 * to the field's top score, and the numeric score. Rows alternate a slight tilt
 * by rank for the hand-made look.
 */
export function StandingRow({
  name,
  score,
  color,
  rank,
  maxScore,
  avatarSize,
  nameSuffix,
  caption,
}: StandingRowProps) {
  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-2 rounded-2xl border-[2.5px] border-ink bg-card px-3 py-2 panel-shadow sm:gap-4 sm:px-4 sm:py-3.5",
        rank % 2 ? "rotate-[0.5deg]" : "-rotate-[0.5deg]",
      )}
    >
      <span
        className="w-8 shrink-0 text-center font-marker text-[22px] sm:w-11 sm:text-3xl"
        style={{
          color: rank === 0 ? "var(--color-amber)" : "var(--muted-foreground)",
        }}
      >
        #{rank + 1}
      </span>
      <PlayerAvatar name={name} color={color} size={avatarSize} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-hand text-xl leading-[0.95] sm:text-2xl">
          {name}
          {nameSuffix}
        </p>
        {caption}
        <div className="mt-1 h-2 overflow-hidden rounded-full border-[1.5px] border-ink bg-muted sm:mt-1.5 sm:h-2.5">
          <div
            className="h-full"
            style={{
              width: `${Math.round((Math.max(0, score) / maxScore) * 100)}%`,
              background: color,
            }}
          />
        </div>
      </div>
      <span
        className="shrink-0 font-marker text-[26px] tabular-nums sm:text-[34px]"
        style={{ color }}
      >
        {score}
      </span>
    </div>
  );
}
