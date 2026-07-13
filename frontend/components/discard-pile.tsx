import { getCardArtUrl } from "@/lib/art";
import type { CardSnapshot } from "@/lib/types";
import { SketchCard } from "./sketch-card";

interface DiscardPileProps {
  topCard: CardSnapshot | undefined;
  count: number;
  roomCode: string;
  onClick?: () => void;
}

const WIDTH = 80;
const HEIGHT = Math.round(WIDTH * 1.4);

/**
 * The felt dock's discard slot. Always occupies the w=80 SketchCard
 * footprint — a dashed ghost outline when empty, the top card once there's
 * something to show — so the dock doesn't resize on the first discard. The
 * whole slot is a single clickable root for a future tap-to-open history
 * modal.
 */
export function DiscardPile({
  topCard,
  count,
  roomCode,
  onClick,
}: DiscardPileProps) {
  return (
    <div
      className="text-center"
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={onClick ? (e) => e.key === "Enter" && onClick() : undefined}
    >
      {topCard ? (
        <SketchCard
          card={topCard}
          w={WIDTH}
          rot={-4}
          showTape={false}
          artUrl={getCardArtUrl(roomCode, topCard)}
          className="mx-auto"
        />
      ) : (
        <div
          className="mx-auto rounded-[7px] border-2 border-dashed border-white/40 bg-white/5"
          style={{ width: WIDTH, height: HEIGHT }}
        />
      )}
      <p className="mt-1 font-hand text-sm text-white">Discard · {count}</p>
    </div>
  );
}
