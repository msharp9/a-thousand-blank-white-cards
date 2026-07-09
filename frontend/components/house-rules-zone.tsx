import { ScrollArea } from "@/components/ui/scroll-area";
import type { CardSnapshot } from "@/lib/types";
import { CardTile } from "./card";

interface HouseRulesZoneProps {
  centerCards: CardSnapshot[];
  brewingCardId: string | null;
}

export function HouseRulesZone({
  centerCards,
  brewingCardId,
}: HouseRulesZoneProps) {
  if (centerCards.length === 0) {
    return (
      <div className="rounded-lg border border-dashed bg-muted/20 p-4 text-center">
        <p className="text-sm text-muted-foreground">
          No house rules yet — play a card to the center to add one.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <p className="text-sm font-medium">House Rules</p>
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
          {centerCards.length}
        </span>
      </div>
      <ScrollArea className="w-full">
        <div className="flex gap-2 pb-2">
          {centerCards.map((card) => (
            <CardTile
              key={card.id}
              card={card}
              brewing={card.id === brewingCardId}
              className="shrink-0"
            />
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
