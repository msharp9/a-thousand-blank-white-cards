"use client";

import { useEffect, useRef, useState } from "react";
import type { DiceRollMsg } from "@/lib/types";
import { cn } from "@/lib/utils";

// How long the dice tumble (cycling random faces) before settling on the
// server's values. The overlay itself is dismissed by the socket layer
// (lib/ws.ts DICE_ROLL_MS), so this only needs to be comfortably shorter.
const TUMBLE_MS = 700;
const TUMBLE_TICK_MS = 80;

interface DiceRollOverlayProps {
  roll: DiceRollMsg;
  actorName: string;
}

/**
 * Dice-roll content for the viewport notice: the dice tumble through random
 * faces, then settle on the server-rolled values with the total. Purely
 * presentational — the authoritative roll arrived in the dice_roll push and
 * is also recorded as a dice_roll history event in the state snapshot.
 */
export function DiceRollOverlay({ roll, actorName }: DiceRollOverlayProps) {
  const [rolling, setRolling] = useState(true);
  const [faces, setFaces] = useState<number[]>(roll.values);
  const rollKeyRef = useRef<DiceRollMsg | null>(null);

  useEffect(() => {
    if (rollKeyRef.current === roll) return;
    rollKeyRef.current = roll;
    setRolling(true);
    const tumble = setInterval(() => {
      setFaces(
        roll.values.map(() => 1 + Math.floor(Math.random() * roll.sides)),
      );
    }, TUMBLE_TICK_MS);
    const settle = setTimeout(() => {
      clearInterval(tumble);
      setFaces(roll.values);
      setRolling(false);
    }, TUMBLE_MS);
    return () => {
      clearInterval(tumble);
      clearTimeout(settle);
    };
  }, [roll]);

  return (
    <div className="flex flex-col items-center gap-2">
      <p className="font-hand text-lg">
        🎲 {actorName} rolls {roll.values.length}d{roll.sides}
      </p>
      <div className="flex items-center gap-2">
        {faces.map((value, i) => (
          <span
            key={i}
            aria-hidden={rolling}
            className={cn(
              "flex size-11 items-center justify-center rounded-lg border-2 border-ink bg-panel-paper font-marker text-2xl tabular-nums sticker-shadow-sm",
              rolling && "animate-wig",
            )}
          >
            {value}
          </span>
        ))}
        {!rolling && roll.values.length > 1 && (
          <span className="font-marker text-2xl tabular-nums text-primary">
            = {roll.total}
          </span>
        )}
      </div>
    </div>
  );
}
