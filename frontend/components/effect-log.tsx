"use client";

import { cn } from "@/lib/utils";

// Log lines authored by the AI arbiter are broadcast with this prefix (see the
// backend's _log_and_broadcast). We render them as commentary rather than as a
// mechanical effect line.
const ARBITER_PREFIX = "🤖 ";

interface EffectLogProps {
  log: string[];
  brewing: string | null;
  className?: string;
}

/**
 * The Play Log panel: a vertical, newest-first list of effect lines. The
 * panel is docked to the bottom of the screen, so new entries prepend at the
 * top edge (the visible edge) rather than appending at the bottom — no
 * auto-scroll is needed to see the latest play.
 */
export function EffectLog({ log, brewing, className }: EffectLogProps) {
  const isEmpty = log.length === 0 && !brewing;

  return (
    <div
      className={cn(
        "border-t-2 border-ink bg-panel-paper px-4 py-2",
        className,
      )}
    >
      <div className="flex items-start gap-2.5">
        <span className="shrink-0 pt-1.5 font-marker text-sm">Play Log</span>
        <div className="flex max-h-[180px] min-w-0 flex-1 flex-col gap-1.5 overflow-y-auto py-1">
          {brewing && (
            <span className="flex items-center gap-1.5 self-start rounded-[10px] border-[1.5px] border-dashed border-ink/50 bg-card/60 px-2.5 py-1 font-hand text-[15px] text-muted-foreground">
              <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
              <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
              <span className="size-1.5 animate-bounce rounded-full bg-current" />
              <span className="ml-1">Interpreting card…</span>
            </span>
          )}
          {isEmpty && (
            <span className="font-hand text-[16px] text-muted-foreground">
              No cards played yet.
            </span>
          )}
          {log
            .slice()
            .reverse()
            .map((entry, i) => {
              const isArbiter = entry.startsWith(ARBITER_PREFIX);
              return (
                <span
                  key={log.length - 1 - i}
                  className={cn(
                    "animate-popin rounded-[10px] border-[1.5px] border-ink bg-card px-2.5 py-1.5 font-hand text-[15px] leading-snug break-words whitespace-pre-wrap",
                    isArbiter &&
                      "border-primary bg-ink font-medium text-background italic",
                  )}
                >
                  {entry}
                </span>
              );
            })}
        </div>
      </div>
    </div>
  );
}
