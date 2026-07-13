import type { CSSProperties } from "react";
import { playerInitial } from "@/lib/players";
import { cn } from "@/lib/utils";

/** Ink-bordered avatar circle in the player's identity color. */
export function PlayerAvatar({
  name,
  color,
  size = 34,
  className,
}: {
  name: string;
  color: string;
  size?: number;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex shrink-0 items-center justify-center rounded-full font-hand text-white",
        className,
      )}
      style={
        {
          width: size,
          height: size,
          background: color,
          border: `${size >= 38 ? 2.5 : 2}px solid #1a1a1a`,
          fontSize: Math.round(size * 0.5),
        } as CSSProperties
      }
    >
      {playerInitial(name)}
    </span>
  );
}
