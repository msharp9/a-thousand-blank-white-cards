import type {
  CardSnapshot,
  GameStateSnapshot,
  PlayerSnapshot,
} from "@/lib/types";

function renderValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null) return "none";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function entries(value: Record<string, unknown> | undefined) {
  return Object.entries(value ?? {});
}

function playerName(players: PlayerSnapshot[], id: string | null | undefined) {
  return players.find((player) => player.id === id)?.name ?? id ?? "everyone";
}

function cardName(cards: Record<string, CardSnapshot>, id: string) {
  return cards[id]?.title || id;
}

export function DynamicStatePanel({
  gameState,
}: {
  gameState: GameStateSnapshot;
}) {
  const { players, cards, rules } = gameState;
  const order = gameState.turn_order.length
    ? gameState.turn_order
    : players.map((player) => player.id);
  const activeId = players.length
    ? players[gameState.turn_index % players.length]?.id
    : undefined;
  const conditionedPlayers = players.filter(
    (player) => entries(player.conditions).length,
  );
  const attributedCards = Object.values(cards).filter(
    (card) => entries(card.attributes).length,
  );
  const mechanicalCards = Object.values(cards).filter(
    (card) => card.mechanical_status,
  );

  return (
    <details className="mx-5 mt-2 rounded-xl border-2 border-dashed border-ink/40 bg-white/90 px-3 py-2 font-hand open:shadow-sm">
      <summary className="cursor-pointer select-none text-base font-semibold">
        Dynamic game state
        <span className="ml-2 text-sm font-normal text-muted-foreground">
          {order.map((id) => playerName(players, id)).join(" → ")}
        </span>
      </summary>
      <div className="mt-2 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
        <section>
          <p className="font-semibold">Turn order</p>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {order.map((id, index) => (
              <span key={id} className="contents">
                {index > 0 && <span aria-hidden>→</span>}
                <span
                  className={
                    id === activeId
                      ? "rounded-md bg-primary px-1.5 text-primary-foreground"
                      : "rounded-md border border-ink/30 bg-white px-1.5"
                  }
                >
                  {playerName(players, id)}
                </span>
              </span>
            ))}
          </div>
        </section>

        <section>
          <p className="font-semibold">Rules</p>
          <div className="mt-1 text-muted-foreground">
            <p>
              Draw {rules.draw} · play {rules.play}
            </p>
            <p>End: {rules.end_condition.type}</p>
            <p>
              Win: {rules.win_condition.kind}
              {rules.win_condition.threshold != null &&
                ` at ${rules.win_condition.threshold}`}
            </p>
            {rules.skip_predicate && <p>Skip: {rules.skip_predicate}</p>}
            {entries(rules.cannot_play).map(([key, value]) => (
              <p key={key}>
                Cannot play · {key}: {renderValue(value)}
              </p>
            ))}
            {entries(rules.extra).map(([key, value]) => (
              <p key={key}>
                {key}: {renderValue(value)}
              </p>
            ))}
          </div>
        </section>

        <section>
          <p className="font-semibold">Conditions & hooks</p>
          <div className="mt-1 text-muted-foreground">
            {conditionedPlayers.length === 0 &&
              gameState.hooks.length === 0 && <p>None active</p>}
            {conditionedPlayers.map((player) => (
              <p key={player.id}>
                {player.name}:{" "}
                {entries(player.conditions)
                  .map(([key, value]) => `${key}=${renderValue(value)}`)
                  .join(", ")}
              </p>
            ))}
            {gameState.hooks.map((hook) => (
              <p key={hook.id} title={hook.id}>
                {hook.event} ·{" "}
                {hook.scope === "player"
                  ? playerName(players, hook.owner_id)
                  : "center"}{" "}
                · {cardName(cards, hook.source_card_id)}
              </p>
            ))}
          </div>
        </section>

        <section>
          <p className="font-semibold">Card attributes</p>
          <div className="mt-1 max-h-24 overflow-y-auto text-muted-foreground">
            {attributedCards.length === 0 && <p>None assigned</p>}
            {attributedCards.map((card) => (
              <p key={card.id}>
                {card.title || card.id}:{" "}
                {entries(card.attributes)
                  .map(([key, value]) => `${key}=${renderValue(value)}`)
                  .join(", ")}
              </p>
            ))}
          </div>
        </section>

        {mechanicalCards.length > 0 && (
          <section className="sm:col-span-2 xl:col-span-4">
            <p className="font-semibold">Card mechanics</p>
            <div className="mt-1 max-h-28 overflow-y-auto text-muted-foreground">
              {mechanicalCards.map((card) => (
                <p key={card.id}>
                  <span className="text-foreground">
                    {card.title || card.id} · {card.mechanical_status}
                  </span>
                  {card.mechanical_reason && ` — ${card.mechanical_reason}`}
                  {card.correlation_id && (
                    <span className="ml-1 font-mono text-[10px]">
                      ({card.correlation_id})
                    </span>
                  )}
                </p>
              ))}
            </div>
          </section>
        )}
      </div>
    </details>
  );
}
