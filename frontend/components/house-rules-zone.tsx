import { getCardArtUrl } from "@/lib/art";
import type { CardSnapshot } from "@/lib/types";
import { SketchCard, stableRotation } from "./sketch-card";

interface HouseRulesZoneProps {
  centerCards: CardSnapshot[];
  brewingCardId: string | null;
  roomCode?: string;
}

/**
 * The felt table's center zone: cards that affect every player, under the
 * "◆ AFFECTS EVERYONE ◆" watermark. Styled for a dark felt background.
 */
export function HouseRulesZone({
  centerCards,
  brewingCardId,
  roomCode,
}: HouseRulesZoneProps) {
  return (
    <div className="relative flex flex-1 flex-col items-center justify-center px-6 py-8">
      <span className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 whitespace-nowrap font-marker text-sm tracking-[3px] text-white/55">
        ◆ AFFECTS EVERYONE ◆
      </span>
      {centerCards.length === 0 ? (
        <p className="rounded-2xl border-2 border-dashed border-white/40 px-10 py-7 text-center font-hand text-[19px] text-white/70">
          Nothing in play for everyone.
          <br />
          Cards that affect all players land here.
        </p>
      ) : (
        <div className="flex flex-wrap items-center justify-center gap-6">
          {centerCards.map((card) => (
            <SketchCard
              key={card.id}
              card={card}
              w={126}
              rot={stableRotation(card.id)}
              brewing={card.id === brewingCardId}
              artUrl={roomCode ? getCardArtUrl(roomCode, card) : null}
              className="animate-popin"
            />
          ))}
        </div>
      )}
    </div>
  );
}
