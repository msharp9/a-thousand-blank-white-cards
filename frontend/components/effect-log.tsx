"use client";

import { useEffect, useRef } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

interface EffectLogProps {
  log: string[];
  brewing: string | null;
  className?: string;
}

export function EffectLog({ log, brewing, className }: EffectLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log.length, brewing]);

  const isEmpty = log.length === 0 && !brewing;

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Effect Log
      </p>
      <ScrollArea className="h-40 w-full rounded-lg border bg-muted/20 p-2">
        {isEmpty && (
          <p className="text-xs italic text-muted-foreground">No events yet.</p>
        )}
        {log.map((entry, i) => (
          <div
            key={i}
            className="border-b border-border/40 py-0.5 text-xs last:border-0"
          >
            {entry}
          </div>
        ))}
        {brewing && (
          <div className="flex items-center gap-1.5 py-0.5 text-xs text-muted-foreground">
            <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
            <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
            <span className="size-1.5 animate-bounce rounded-full bg-current" />
            <span className="ml-1">Interpreting card…</span>
          </div>
        )}
        <div ref={bottomRef} />
      </ScrollArea>
    </div>
  );
}
