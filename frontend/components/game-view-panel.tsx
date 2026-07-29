"use client";

import { GalleryOverlay } from "@/components/gallery-overlay";
import { type AuxiliaryGameView } from "@/components/game-nav-tabs";
import { HostControlOverlay } from "@/components/host-control-overlay";
import { type PanelPresentation } from "@/components/overlay-shell";
import { PlayLogPanel } from "@/components/play-log-panel";
import { ScoreboardOverlay } from "@/components/scoreboard-overlay";
import { StatusConditionsOverlay } from "@/components/status-conditions-overlay";
import type { ClientMsg, GameStateSnapshot } from "@/lib/types";

interface GameViewPanelProps {
  view: AuxiliaryGameView;
  gameState: GameStateSnapshot;
  roomCode: string;
  log: string[];
  brewing: string | null;
  presentation: PanelPresentation;
  godMode?: boolean;
  godModeLoading?: boolean;
  send: (message: ClientMsg) => void;
  onClose: () => void;
}

export function GameViewPanel({
  view,
  gameState,
  roomCode,
  log,
  brewing,
  presentation,
  godMode = false,
  godModeLoading = false,
  send,
  onClose,
}: GameViewPanelProps) {
  switch (view) {
    case "log":
      return (
        <PlayLogPanel
          log={log}
          brewing={brewing}
          presentation={presentation}
          onClose={onClose}
        />
      );
    case "gallery":
      return (
        <GalleryOverlay
          gameState={gameState}
          roomCode={roomCode}
          presentation={presentation}
          onClose={onClose}
        />
      );
    case "scores":
      return (
        <ScoreboardOverlay
          players={gameState.players}
          presentation={presentation}
          onClose={onClose}
        />
      );
    case "status":
      return (
        <StatusConditionsOverlay
          gameState={gameState}
          presentation={presentation}
          onClose={onClose}
        />
      );
    case "host":
      return (
        <HostControlOverlay
          gameState={gameState}
          send={send}
          presentation={presentation}
          godMode={godMode}
          godModeLoading={godModeLoading}
          onClose={onClose}
        />
      );
  }
}
