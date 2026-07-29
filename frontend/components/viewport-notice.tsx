"use client";

import { useCallback, useEffect, useRef } from "react";
import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DiceRollOverlay } from "@/components/dice-roll-overlay";
import type { PlayerSnapshot } from "@/lib/types";
import type { ViewportNotice } from "@/lib/notices";
import { cn } from "@/lib/utils";

interface ViewportNoticeHostProps {
  topNotices: ViewportNotice[];
  arbiterNotices: ViewportNotice[];
  players: PlayerSnapshot[];
  onDismiss: (id: string) => void;
  stackedGameNav?: boolean;
}

export function ViewportNoticeHost({
  topNotices,
  arbiterNotices,
  players,
  onDismiss,
  stackedGameNav = false,
}: ViewportNoticeHostProps) {
  const top = topNotices[0];
  const arbiter = arbiterNotices[0];

  return (
    <div
      data-viewport-notices
      className="pointer-events-none fixed inset-0 z-[70]"
      aria-live="off"
    >
      {top && (
        <div
          data-notice-lane="top"
          className={cn(
            "absolute inset-x-0 top-[calc(env(safe-area-inset-top)+7.25rem)] flex justify-center px-[max(0.75rem,env(safe-area-inset-left))]",
            stackedGameNav ? "xl:top-[4.5rem]" : "sm:top-[4.5rem]",
          )}
        >
          <NoticeBubble notice={top} players={players} onDismiss={onDismiss} />
        </div>
      )}
      {arbiter && (
        <div
          data-notice-lane="arbiter"
          className="absolute inset-x-0 bottom-[calc(env(safe-area-inset-bottom)+4.75rem)] flex justify-center px-[max(0.75rem,env(safe-area-inset-left))] sm:bottom-[calc(env(safe-area-inset-bottom)+1rem)]"
        >
          <NoticeBubble
            notice={arbiter}
            players={players}
            onDismiss={onDismiss}
          />
        </div>
      )}
    </div>
  );
}

function useAutoDismiss(
  notice: ViewportNotice,
  onDismiss: (id: string) => void,
) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const remainingRef = useRef<number>(notice.timeoutMs);
  const startedRef = useRef(0);
  const pausedRef = useRef(false);

  const clearTimer = useCallback(() => {
    if (!timerRef.current) return;
    clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  const resume = useCallback(() => {
    if (document.hidden || !pausedRef.current) return;
    pausedRef.current = false;
    startedRef.current = Date.now();
    timerRef.current = setTimeout(
      () => onDismiss(notice.id),
      remainingRef.current,
    );
  }, [notice.id, onDismiss]);

  const pause = useCallback(() => {
    if (pausedRef.current) return;
    pausedRef.current = true;
    remainingRef.current = Math.max(
      0,
      remainingRef.current - (Date.now() - startedRef.current),
    );
    clearTimer();
  }, [clearTimer]);

  useEffect(() => {
    remainingRef.current = notice.timeoutMs;
    pausedRef.current = false;
    startedRef.current = Date.now();
    timerRef.current = setTimeout(() => onDismiss(notice.id), notice.timeoutMs);
    const onVisibilityChange = () => (document.hidden ? pause() : resume());
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [clearTimer, notice.id, notice.timeoutMs, onDismiss, pause, resume]);

  return { pause, resume };
}

function reactionText(
  notice: Extract<ViewportNotice, { kind: "reaction" }>,
  players: PlayerSnapshot[],
) {
  const reactor =
    players.find((player) => player.id === notice.result.reactor_id)?.name ??
    "Someone";
  switch (notice.result.outcome) {
    case "countered":
      return `💥 Countered by ${reactor}!`;
    case "stolen":
      return `🫳 ${reactor} stole the card!`;
    case "redirected":
      return `↩️ ${reactor} redirected it!`;
    default:
      return "The reaction resolved.";
  }
}

function NoticeBubble({
  notice,
  players,
  onDismiss,
}: {
  notice: ViewportNotice;
  players: PlayerSnapshot[];
  onDismiss: (id: string) => void;
}) {
  const { pause, resume } = useAutoDismiss(notice, onDismiss);
  const isError = notice.kind === "error";
  const actorName =
    notice.kind === "dice"
      ? (players.find((player) => player.id === notice.roll.actor_id)?.name ??
        "Someone")
      : "";

  return (
    <div
      role={isError ? "alert" : "status"}
      className={cn(
        "pointer-events-auto relative flex max-w-xl animate-popin items-start gap-2 rounded-2xl border-[2.5px] border-ink bg-card px-4 py-3 pr-12 panel-shadow",
        notice.kind === "arbiter" &&
          "max-w-lg -rotate-[0.5deg] bg-ink text-background",
        isError && "text-destructive",
        notice.kind === "admin" &&
          notice.outcome === "applied" &&
          "border-marker-green",
        notice.kind === "admin" &&
          notice.outcome !== "applied" &&
          "border-amber",
      )}
      onMouseEnter={pause}
      onMouseLeave={resume}
      onFocusCapture={pause}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) resume();
      }}
    >
      {notice.kind === "dice" ? (
        <DiceRollOverlay roll={notice.roll} actorName={actorName} />
      ) : notice.kind === "arbiter" ? (
        <div>
          <p className="font-hand text-xs uppercase tracking-wider opacity-70">
            🤖 Arbiter
          </p>
          <p className="font-hand text-lg leading-snug italic">
            {notice.message}
          </p>
        </div>
      ) : (
        <p className="font-hand text-lg leading-snug">
          {notice.kind === "reaction"
            ? reactionText(notice, players)
            : notice.message}
        </p>
      )}
      <Button
        variant="ghost"
        size="icon-sm"
        className={cn(
          "absolute top-1.5 right-1.5 size-11 sm:size-8",
          notice.kind === "arbiter" &&
            "text-background hover:bg-background/15 hover:text-background",
          isError && "text-destructive hover:bg-destructive/10",
        )}
        onClick={() => onDismiss(notice.id)}
      >
        <XIcon />
        <span className="sr-only">Dismiss notification</span>
      </Button>
    </div>
  );
}
