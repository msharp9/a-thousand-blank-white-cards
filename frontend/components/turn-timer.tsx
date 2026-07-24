"use client";

import { useEffect, useState } from "react";
import type { TurnTimerSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Live ms-remaining against the server deadline, ticked every 250ms. The
 * server is authoritative (expiry there force-ends the turn whether or not
 * this countdown agrees); each turn_timer push / state snapshot re-syncs the
 * deadline, so reconnects and clock pauses never desync the display. */
function useCountdown(deadlineEpochMs: number): number {
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, deadlineEpochMs - Date.now()),
  );
  useEffect(() => {
    const tick = () => setRemaining(Math.max(0, deadlineEpochMs - Date.now()));
    tick();
    const interval = setInterval(tick, 250);
    return () => clearInterval(interval);
  }, [deadlineEpochMs]);
  return remaining;
}

/**
 * Turn-bar chip for the pausable turn clock (rules.turn_timer): a seconds
 * countdown while the active player's clock runs, a paused badge while the
 * server has it suspended (brewing, reaction window, interaction), nothing
 * when no clock is armed.
 */
export function TurnTimerChip({ timer }: { timer: TurnTimerSnapshot | null }) {
  if (!timer) return null;
  if (timer.paused) {
    return (
      <span
        className="rounded-lg border-[1.5px] border-ink bg-panel-paper px-2 py-0.5 font-hand text-sm text-muted-foreground"
        title="Turn clock paused while the table waits"
      >
        ⏸ clock paused
      </span>
    );
  }
  if (timer.deadline_epoch_ms === null) return null;
  return <RunningChip deadlineEpochMs={timer.deadline_epoch_ms} />;
}

function RunningChip({ deadlineEpochMs }: { deadlineEpochMs: number }) {
  const remaining = useCountdown(deadlineEpochMs);
  const seconds = Math.ceil(remaining / 1000);
  return (
    <span
      className={cn(
        "rounded-lg border-[1.5px] border-ink bg-panel-paper px-2 py-0.5 font-mono text-sm tabular-nums",
        seconds <= 10 && "text-destructive",
      )}
      title="Time left in this turn"
    >
      ⏱ {seconds}s
    </span>
  );
}
