"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type PanelPresentation = "modal" | "sidebar";

interface OverlayShellProps {
  scrimTestId: string;
  title: string;
  subtitle?: string;
  closeLabel: string;
  onClose: () => void;
  // Panel width class (the only shell dimension that differs per overlay).
  panelClassName?: string;
  presentation?: PanelPresentation;
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
  presentation = "modal",
  children,
}: OverlayShellProps) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const modal = presentation === "modal";

  useEffect(() => {
    if (modal) closeRef.current?.focus({ preventScroll: true });
  }, [modal]);

  return (
    <div
      data-testid={modal ? scrimTestId : undefined}
      data-game-panel-shell
      data-presentation={presentation}
      className={cn(
        modal
          ? "fixed inset-0 z-[45] flex items-stretch justify-center bg-[rgba(20,18,14,0.55)] p-0 sm:p-4"
          : "relative flex h-full w-[420px] shrink-0 items-stretch 2xl:w-[480px]",
      )}
      onClick={modal ? onClose : undefined}
    >
      <section
        id="game-view-panel"
        role={modal ? "dialog" : "complementary"}
        aria-modal={modal ? "true" : undefined}
        aria-labelledby={titleId}
        className={cn(
          "flex w-full flex-col overflow-hidden bg-card",
          modal
            ? "border-[3px] border-ink pt-[env(safe-area-inset-top)] pr-[env(safe-area-inset-right)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] shadow-[8px_8px_0_rgba(26,26,26,0.8)] sm:rounded-[18px] sm:p-0"
            : "border-l-[3px] border-ink",
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
        <div
          data-game-panel-body
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3 sm:px-5 sm:py-5"
        >
          {children}
        </div>
      </section>
    </div>
  );
}
