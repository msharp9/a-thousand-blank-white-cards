"use client";

import { cn } from "@/lib/utils";

/**
 * Header pill naming the active player, sourced from players[turn_index].
 * A polite atomic live region: each turn change announces one concise line
 * ("YOUR TURN · Turn 12" / "Alice's turn · Turn 12") to screen readers. Text
 * carries the state on its own; the identity-color dot and the inverted
 * your-turn treatment are reinforcement, never the sole signal.
 */
export function CurrentTurnIndicator({
  activeName,
  isViewer,
  turnNumber,
  color,
  className,
}: {
  activeName: string | undefined;
  isViewer: boolean;
  turnNumber: number;
  color?: string;
  className?: string;
}) {
  return (
    <span
      data-current-turn-indicator
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className={cn(
        "flex min-w-0 items-center gap-1.5 rounded-lg border-2 border-ink px-2.5 py-1 font-hand text-sm leading-none",
        isViewer ? "bg-ink font-bold text-background" : "bg-panel-paper",
        !activeName && "hidden",
        className,
      )}
    >
      {activeName && (
        <>
          {color && (
            <span
              aria-hidden
              className="size-2.5 shrink-0 rounded-full border border-current"
              style={{ backgroundColor: color }}
            />
          )}
          <span className="min-w-0 truncate">
            {isViewer ? "YOUR TURN" : `${activeName}'s turn`} · Turn{" "}
            {turnNumber}
          </span>
        </>
      )}
    </span>
  );
}

/** Seat/zone badge marking the active player's spot on the table itself.
 * Inverted ink-on-paper so it reads apart from the muted seat-edge labels and
 * from DnD hover (identity-color ring + scale). */
export function CurrentTurnBadge({
  label = "Current turn",
}: {
  label?: string;
}) {
  return (
    <span
      data-current-turn-badge
      className="whitespace-nowrap rounded-md border-[1.5px] border-ink bg-ink px-1.5 py-0.5 font-hand text-[11px] font-bold uppercase tracking-wide text-background"
    >
      {label}
    </span>
  );
}
