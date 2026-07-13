"use client";

import { useState, type ReactNode } from "react";
import {
  DndContext,
  DragOverlay,
  MouseSensor,
  TouchSensor,
  pointerWithin,
  useDndContext,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { PlayBlankDialog } from "@/components/play-blank-dialog";
import { SketchCard } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import {
  FELT_DROP_ID,
  executeDrop,
  planDrop,
  playMessage,
  type DropPlay,
} from "@/lib/dnd";
import type { CardSnapshot, ClientMsg } from "@/lib/types";
import { cn } from "@/lib/utils";

interface PlayDndContextProps {
  cards: Record<string, CardSnapshot>;
  roomCode: string;
  send: (msg: ClientMsg) => void;
  children: ReactNode;
}

/**
 * Wraps the playing view so hand cards can be dragged onto the felt (general
 * play) or an opponent seat (targeted play). Click-to-select-and-Play keeps
 * working unchanged: the mouse sensor only activates after 8px of movement,
 * and the touch sensor needs a long-press so scrolling isn't hijacked. A
 * dropped BLANK opens the same author-on-play dialog as clicking Play on it.
 */
export function PlayDndContext({
  cards,
  roomCode,
  send,
  children,
}: PlayDndContextProps) {
  const [activeCard, setActiveCard] = useState<CardSnapshot | null>(null);
  const [pendingBlank, setPendingBlank] = useState<DropPlay | null>(null);

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, {
      activationConstraint: { delay: 250, tolerance: 8 },
    }),
  );

  function handleDragStart(event: DragStartEvent) {
    setActiveCard(cards[String(event.active.id)] ?? null);
  }

  function handleDragEnd(event: DragEndEvent) {
    setActiveCard(null);
    executeDrop(planDrop(event, cards), send, setPendingBlank);
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={pointerWithin}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveCard(null)}
    >
      {children}
      <DragOverlay dropAnimation={null}>
        {activeCard && (
          <div className="scale-105 cursor-grabbing drop-shadow-[0_14px_22px_rgba(20,18,14,0.35)]">
            <SketchCard
              card={activeCard}
              w={130}
              artUrl={getCardArtUrl(roomCode, activeCard)}
            />
          </div>
        )}
      </DragOverlay>
      <PlayBlankDialog
        open={Boolean(pendingBlank)}
        onOpenChange={(open) => {
          if (!open) setPendingBlank(null);
        }}
        onPlay={(title, description, art) => {
          if (!pendingBlank) return;
          send(playMessage(pendingBlank, { title, description, art }));
          setPendingBlank(null);
        }}
      />
    </DndContext>
  );
}

/**
 * The felt table as a drop target for a general (untargeted) play. Hints with
 * a dashed outline while any card is being dragged and glows when the card is
 * over it; a drop plays the card exactly like the Play button.
 */
export function FeltDropZone({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: FELT_DROP_ID,
    data: { type: "felt" },
  });
  const { active } = useDndContext();

  return (
    <div
      ref={setNodeRef}
      data-felt-drop
      className={cn(
        className,
        "transition-shadow duration-150",
        active && "outline-dashed outline-2 outline-offset-4 outline-ink/40",
        isOver &&
          "shadow-[inset_0_0_60px_rgba(255,255,255,0.3),0_0_0_3px_rgba(255,255,255,0.55)]",
      )}
    >
      {children}
    </div>
  );
}
