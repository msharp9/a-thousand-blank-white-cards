"use client";

import type { ReactNode } from "react";
import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface OverlayShellProps {
  scrimTestId: string;
  title: string;
  subtitle?: string;
  closeLabel: string;
  onClose: () => void;
  // Panel width class (the only shell dimension that differs per overlay).
  panelClassName?: string;
  children: ReactNode;
}

/**
 * The full-screen scrim + framed paper panel shared by the Gallery and
 * Scoreboard overlays (design's Gallery/Scores tabs): a dimmed backdrop that
 * closes on tap, a bordered card that swallows inner clicks, and a header with
 * a title/subtitle and a close button. Body content scrolls independently.
 */
export function OverlayShell({
  scrimTestId,
  title,
  subtitle,
  closeLabel,
  onClose,
  panelClassName,
  children,
}: OverlayShellProps) {
  return (
    <div
      data-testid={scrimTestId}
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-[rgba(20,18,14,0.55)] p-4"
      onClick={onClose}
    >
      <div
        className={cn(
          "flex w-full flex-col overflow-hidden rounded-[18px] border-[3px] border-ink bg-card shadow-[8px_8px_0_rgba(26,26,26,0.8)]",
          panelClassName,
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between border-b-2 border-ink px-5 py-3.5">
          <div>
            <h2 className="font-marker text-2xl">{title}</h2>
            {subtitle && (
              <p className="font-hand text-[15px] text-muted-foreground">
                {subtitle}
              </p>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onClose}
            aria-label={closeLabel}
          >
            <XIcon />
          </Button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-5">{children}</div>
      </div>
    </div>
  );
}
