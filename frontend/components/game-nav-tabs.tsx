"use client";

import { useEffect, useState } from "react";
import { GalleryOverlay } from "@/components/gallery-overlay";
import { HostControlOverlay } from "@/components/host-control-overlay";
import { ScoreboardOverlay } from "@/components/scoreboard-overlay";
import { StatusConditionsOverlay } from "@/components/status-conditions-overlay";
import type { ClientMsg, GameStateSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

type Tab = "table" | "gallery" | "scores" | "status" | "host";

const PLAYER_TABS: { id: Tab; label: string }[] = [
  { id: "table", label: "Table" },
  { id: "gallery", label: "Gallery" },
  { id: "scores", label: "Scores" },
  { id: "status", label: "Status" },
];

interface GameNavTabsProps {
  gameState: GameStateSnapshot;
  roomCode: string;
  isHost: boolean;
  send: (message: ClientMsg) => void;
  className?: string;
}

/**
 * Top-bar view switcher for the playing phase. Create and Epilogue are
 * separate phases, not tabs. Table is the default felt view underneath;
 * Gallery, Scores, Status, and Host render as full-screen overlays and close
 * back to Table. The felt/hand stay mounted while switching views.
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
        className={cn("flex items-center gap-1", className)}
        aria-label="Game views"
      >
        {tabs.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
            className={cn(
              "min-h-11 min-w-0 flex-1 rounded-lg border-[1.5px] border-ink px-1 py-1 font-hand text-[13px] transition-colors min-[360px]:text-[15px] sm:min-h-0 sm:flex-none sm:px-2.5",
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
      {tab === "status" && (
        <StatusConditionsOverlay
          gameState={gameState}
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
