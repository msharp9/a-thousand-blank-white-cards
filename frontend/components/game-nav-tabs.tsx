"use client";

import { useEffect, useState } from "react";
import { GalleryOverlay } from "@/components/gallery-overlay";
import { HostControlOverlay } from "@/components/host-control-overlay";
import { ScoreboardOverlay } from "@/components/scoreboard-overlay";
import type { ClientMsg, GameStateSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

type Tab = "table" | "gallery" | "scores" | "host";

const PLAYER_TABS: { id: Tab; label: string }[] = [
  { id: "table", label: "Table" },
  { id: "gallery", label: "Gallery" },
  { id: "scores", label: "Scores" },
];

interface GameNavTabsProps {
  gameState: GameStateSnapshot;
  roomCode: string;
  isHost: boolean;
  send: (message: ClientMsg) => void;
  className?: string;
}

/**
 * Top-bar view switcher for the playing phase (design's Table/Gallery/Scores
 * tabs — Create and Epilogue are separate phases in this app, not tabs).
 * Table is the default felt view underneath; Gallery and Scores render as
 * full-screen overlays on top of it and close back to Table via the tab, the
 * Escape key, or a scrim tap. The felt/hand stay mounted the whole time, so
 * switching tabs never interrupts live game state — the overlays just read
 * straight from the same gameState this component already re-renders on.
 */
export function GameNavTabs({
  gameState,
  roomCode,
  isHost,
  send,
  className,
}: GameNavTabsProps) {
  const [tab, setTab] = useState<Tab>("table");
  const tabs = isHost
    ? [...PLAYER_TABS, { id: "host" as const, label: "Host" }]
    : PLAYER_TABS;

  useEffect(() => {
    if (tab === "table") return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setTab("table");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab]);

  return (
    <>
      <nav
        className={cn("flex items-center gap-1.5", className)}
        aria-label="Game views"
      >
        {tabs.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
            className={cn(
              "min-h-11 flex-1 rounded-lg border-[1.5px] border-ink px-2.5 py-1 font-hand text-[15px] transition-colors sm:min-h-0 sm:flex-none",
              tab === id
                ? "bg-ink text-background"
                : "bg-card text-foreground hover:bg-muted",
            )}
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === "gallery" && (
        <GalleryOverlay
          gameState={gameState}
          roomCode={roomCode}
          onClose={() => setTab("table")}
        />
      )}
      {tab === "scores" && (
        <ScoreboardOverlay
          players={gameState.players}
          onClose={() => setTab("table")}
        />
      )}
      {tab === "host" && (
        <HostControlOverlay
          gameState={gameState}
          send={send}
          onClose={() => setTab("table")}
        />
      )}
    </>
  );
}
