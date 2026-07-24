import { getCardArtUrl } from "@/lib/art";
import type { CardSnapshot } from "@/lib/types";
import { SketchCard } from "./sketch-card";

interface ExilePileProps {
  topCard: CardSnapshot | undefined;
  count: number;
  roomCode: string;
}

const WIDTH = 80;
const HEIGHT = Math.round(WIDTH * 1.4);

/**
 * The felt dock's "removed from game" slot (the public exile zone). Sits next
 * to the discard pile in the same w=80 SketchCard footprint and, like it,
 * shows a dashed ghost outline when empty so the dock doesn't resize when the
 * first card is exiled. The card face is dimmed to read as out of the game —
 * unlike the discard pile, nothing comes back from here.
 */
export function ExilePile({ topCard, count, roomCode }: ExilePileProps) {
  return (
    <div className="text-center">
      {topCard ? (
        <SketchCard
          card={topCard}
          w={WIDTH}
          rot={4}
          showTape={false}
          artUrl={getCardArtUrl(roomCode, topCard)}
          className="mx-auto opacity-60 grayscale"
        />
      ) : (
        <div
          className="mx-auto rounded-[7px] border-2 border-dashed border-white/25 bg-white/5"
          style={{ width: WIDTH, height: HEIGHT }}
        />
      )}
      <p className="mt-1 font-hand text-sm text-white/80">Removed · {count}</p>
    </div>
  );
}
