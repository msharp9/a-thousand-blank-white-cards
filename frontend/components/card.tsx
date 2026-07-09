import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import type { CardSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

interface CardProps {
  card: CardSnapshot;
  brewing?: boolean;
  selectable?: boolean;
  onClick?: () => void;
  className?: string;
}

export function CardTile({ card, brewing, selectable, onClick, className }: CardProps) {
  return (
    <div
      className={cn(
        "relative flex min-h-[96px] w-40 flex-col gap-1 rounded-lg border bg-card p-3 shadow-sm",
        selectable && "cursor-pointer transition-all hover:border-primary hover:shadow-md",
        brewing && "opacity-70",
        className,
      )}
      onClick={selectable ? onClick : undefined}
      role={selectable ? "button" : undefined}
      tabIndex={selectable ? 0 : undefined}
      onKeyDown={selectable && onClick ? (e) => e.key === "Enter" && onClick() : undefined}
    >
      <p className="text-sm font-semibold leading-tight">{card.title}</p>
      <p className="line-clamp-4 text-xs leading-snug text-muted-foreground">{card.description}</p>
      {card.verdict && card.verdict !== "ok" && (
        <Badge variant="destructive" className="mt-auto text-[10px]">
          {card.verdict}
        </Badge>
      )}
      {brewing && (
        <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-background/60">
          <Spinner />
        </div>
      )}
    </div>
  );
}
