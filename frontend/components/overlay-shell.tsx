"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
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
 * Full-screen scrim and framed paper panel shared by the playing-phase
 * overlays. The backdrop closes on tap, the bordered panel swallows inner
 * clicks, and body content scrolls independently.
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
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus({ preventScroll: true });
  }, []);

  return (
    <div
      data-testid={scrimTestId}
      className="fixed inset-0 z-[60] flex items-stretch justify-center bg-[rgba(20,18,14,0.55)] p-0 sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={cn(
          "flex w-full flex-col overflow-hidden border-[3px] border-ink bg-card pt-[env(safe-area-inset-top)] pr-[env(safe-area-inset-right)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] shadow-[8px_8px_0_rgba(26,26,26,0.8)] sm:rounded-[18px] sm:p-0",
          panelClassName,
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between border-b-2 border-ink px-3 py-2 sm:px-5 sm:py-3.5">
          <div>
            <h2 id={titleId} className="font-marker text-xl sm:text-2xl">
              {title}
            </h2>
            {subtitle && (
              <p className="font-hand text-[15px] text-muted-foreground">
                {subtitle}
              </p>
            )}
          </div>
          <Button
            ref={closeRef}
            variant="ghost"
            size="icon"
            className="size-11 sm:size-8"
            onClick={onClose}
            aria-label={closeLabel}
          >
            <XIcon />
          </Button>
        </header>
        <div className="flex-1 overflow-y-auto overscroll-contain px-3 py-3 sm:px-5 sm:py-5">
          {children}
        </div>
      </div>
    </div>
  );
}
