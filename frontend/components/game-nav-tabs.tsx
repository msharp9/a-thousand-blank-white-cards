"use client";

import { cn } from "@/lib/utils";

export type GameView =
  "table" | "log" | "gallery" | "scores" | "status" | "host";

export type AuxiliaryGameView = Exclude<GameView, "table">;

const PLAYER_VIEWS: {
  id: GameView;
  label: string;
}[] = [
  { id: "table", label: "Table" },
  { id: "log", label: "History" },
  { id: "gallery", label: "Gallery" },
  { id: "scores", label: "Scores" },
  { id: "status", label: "Rules" },
];

interface GameNavTabsProps {
  activeView: GameView;
  isHost: boolean;
  onViewChange: (view: GameView) => void;
  className?: string;
}

export function GameNavTabs({
  activeView,
  isHost,
  onViewChange,
  className,
}: GameNavTabsProps) {
  const views = isHost
    ? [...PLAYER_VIEWS, { id: "host" as const, label: "Host" }]
    : PLAYER_VIEWS;

  return (
    <nav
      className={cn("flex items-center gap-1", className)}
      aria-label="Game views"
    >
      {views.map(({ id, label }) => {
        const selected = activeView === id;
        return (
          <button
            key={id}
            type="button"
            data-game-view-trigger={id}
            aria-pressed={selected}
            aria-expanded={id === "table" ? undefined : selected}
            aria-controls={id === "table" ? undefined : "game-view-panel"}
            onClick={() => onViewChange(id)}
            className={cn(
              "min-h-11 min-w-0 flex-1 rounded-lg border-[1.5px] border-ink px-1 py-1 font-hand text-[13px] transition-colors min-[360px]:text-[15px] xl:min-h-0 xl:flex-none xl:px-2.5",
              selected
                ? "bg-ink text-background"
                : "bg-card text-foreground hover:bg-muted",
            )}
          >
            {label}
          </button>
        );
      })}
    </nav>
  );
}
