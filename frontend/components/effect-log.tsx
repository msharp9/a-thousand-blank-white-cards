"use client";

import { useEffect, useRef } from "react";
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
 * The Play Log strip: a horizontally scrolling row of paper chips, one per
 * effect line, auto-scrolled to the newest entry (entries append at the end).
 */
export function EffectLog({ log, brewing, className }: EffectLogProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTo({ left: el.scrollWidth, behavior: "smooth" });
  }, [log.length, brewing]);

  const isEmpty = log.length === 0 && !brewing;

  return (
    <div
      className={cn(
        "border-t-2 border-ink bg-panel-paper px-4 py-2",
        className,
      )}
    >
      <div className="flex items-center gap-2.5">
        <span className="shrink-0 font-marker text-sm">Play Log</span>
        <div
          ref={scrollerRef}
          className="flex items-center gap-2 overflow-x-auto px-0.5 py-1"
        >
          {isEmpty && (
            <span className="whitespace-nowrap font-hand text-[15px] text-[#999]">
              No cards played yet.
            </span>
          )}
          {log.map((entry, i) => {
            const isArbiter = entry.startsWith(ARBITER_PREFIX);
            return (
              <span
                key={i}
                className={cn(
                  "shrink-0 rounded-[10px] border-[1.5px] border-ink bg-white px-2.5 py-1 font-hand text-[13px] leading-tight whitespace-nowrap",
                  isArbiter && "bg-accent/40 italic",
                )}
              >
                {entry}
              </span>
            );
          })}
          {brewing && (
            <span className="flex shrink-0 items-center gap-1.5 rounded-[10px] border-[1.5px] border-dashed border-ink/50 bg-white/60 px-2.5 py-1 font-hand text-[13px] text-muted-foreground">
              <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
              <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
              <span className="size-1.5 animate-bounce rounded-full bg-current" />
              <span className="ml-1">Interpreting card…</span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
