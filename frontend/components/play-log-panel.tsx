"use client";

import { EffectLog } from "@/components/effect-log";
import {
  OverlayShell,
  type PanelPresentation,
} from "@/components/overlay-shell";

interface PlayLogPanelProps {
  log: string[];
  brewing: string | null;
  presentation: PanelPresentation;
  onClose: () => void;
}

export function PlayLogPanel({
  log,
  brewing,
  presentation,
  onClose,
}: PlayLogPanelProps) {
  return (
    <OverlayShell
      scrimTestId="play-log-scrim"
      title="Play Log"
      subtitle="Newest plays first"
      closeLabel="Close Play Log"
      presentation={presentation}
      onClose={onClose}
      panelClassName="max-w-[720px]"
    >
      <EffectLog log={log} brewing={brewing} variant="panel" />
    </OverlayShell>
  );
}
