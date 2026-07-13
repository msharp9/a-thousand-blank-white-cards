import { ScrollArea } from "@/components/ui/scroll-area";
import { getCardArtUrl } from "@/lib/art";
import type { CardSnapshot } from "@/lib/types";
import { SketchCard, stableRotation } from "./sketch-card";

interface HouseRulesZoneProps {
  centerCards: CardSnapshot[];
  brewingCardId: string | null;
  roomCode?: string;
}

export function HouseRulesZone({
  centerCards,
  brewingCardId,
  roomCode,
}: HouseRulesZoneProps) {
  if (centerCards.length === 0) {
    return (
      <div className="rounded-lg border-2 border-dashed border-ink/40 bg-white/40 p-6 text-center">
        <p className="font-hand text-base text-muted-foreground">
          Nothing in play for everyone…
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <p className="font-marker text-sm">House Rules</p>
        <span className="rounded-full bg-muted px-2 py-0.5 font-hand text-xs text-muted-foreground">
          {centerCards.length}
        </span>
      </div>
      <ScrollArea className="w-full">
        <div className="flex gap-4 px-2 pb-4 pt-3">
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
      </ScrollArea>
    </div>
  );
}
