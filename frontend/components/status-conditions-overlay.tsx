"use client";

import {
  OverlayShell,
  type PanelPresentation,
} from "@/components/overlay-shell";
import { PlayerAvatar } from "@/components/player-avatar";
import {
  activeConditions,
  conditionName,
  conditionSentence,
  conditionValueDetail,
  hookTitle,
} from "@/lib/conditions";
import { playerColor } from "@/lib/players";
import { coreRuleSentences } from "@/lib/rules";
import type { GameStateSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

interface StatusConditionsOverlayProps {
  gameState: GameStateSnapshot;
  presentation?: PanelPresentation;
  onClose: () => void;
}

export function StatusConditionsOverlay({
  gameState,
  presentation = "modal",
  onClose,
}: StatusConditionsOverlayProps) {
  const conditionedPlayers = gameState.players
    .map((player, index) => ({
      player,
      index,
      conditions: activeConditions(player.conditions),
    }))
    .filter(({ conditions }) => conditions.length > 0);

  return (
    <OverlayShell
      scrimTestId="status-conditions-scrim"
      title="Rules & Status"
      subtitle="The rules and effects shaping this game."
      closeLabel="Close rules and status"
      onClose={onClose}
      presentation={presentation}
      panelClassName="max-w-[760px]"
    >
      <div className="flex flex-col gap-6">
        <section aria-labelledby="core-rules-heading">
          <h3
            id="core-rules-heading"
            className="font-marker text-lg sm:text-xl"
          >
            Core Rules
          </h3>
          <ul className="mt-2 flex flex-col gap-1.5 rounded-xl border-2 border-ink bg-panel-paper p-4 panel-shadow">
            {coreRuleSentences(gameState.rules).map((sentence, index) => (
              <li
                key={`${index}-${sentence}`}
                className="font-hand text-lg leading-snug"
              >
                {sentence}
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="player-conditions-heading">
          <h3
            id="player-conditions-heading"
            className="font-marker text-lg sm:text-xl"
          >
            Players
          </h3>
          {conditionedPlayers.length === 0 ? (
            <p className="mt-2 rounded-xl border-2 border-dashed border-ink/40 bg-panel-paper p-4 font-hand text-muted-foreground">
              No active status conditions.
            </p>
          ) : (
            <div
              className={cn(
                "mt-2 grid grid-cols-1 gap-3",
                presentation === "modal" && "sm:grid-cols-2",
              )}
            >
              {conditionedPlayers.map(({ player, index, conditions }) => (
                <article
                  key={player.id}
                  className="rounded-xl border-2 border-ink bg-panel-paper p-3 panel-shadow"
                >
                  <div className="flex items-center gap-2.5">
                    <PlayerAvatar
                      name={player.name}
                      color={playerColor(index)}
                      size={38}
                    />
                    <h4 className="font-marker text-base">{player.name}</h4>
                  </div>
                  <ul className="mt-3 flex flex-col gap-2">
                    {conditions.map(([key, value]) => {
                      const detail = conditionValueDetail(value);
                      return (
                        <li
                          key={key}
                          className="rounded-lg border-[1.5px] border-ink/40 bg-card px-3 py-2"
                        >
                          <p className="font-hand text-lg leading-snug">
                            {conditionSentence(
                              player.name,
                              key,
                              player.condition_ttls?.[key],
                            )}
                          </p>
                          {detail && (
                            <p className="font-hand text-sm text-muted-foreground">
                              {detail}
                            </p>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </article>
              ))}
            </div>
          )}
        </section>

        <section aria-labelledby="reactionary-rules-heading">
          <h3
            id="reactionary-rules-heading"
            className="font-marker text-lg sm:text-xl"
          >
            Reactionary Rules
          </h3>
          {gameState.hooks.length === 0 ? (
            <p className="mt-2 rounded-xl border-2 border-dashed border-ink/40 bg-panel-paper p-4 font-hand text-muted-foreground">
              No reactionary rules are active.
            </p>
          ) : (
            <div className="mt-2 flex flex-col gap-3">
              {gameState.hooks.map((hook) => {
                const owner = gameState.players.find(
                  (player) => player.id === hook.owner_id,
                );
                const source = gameState.cards[hook.source_card_id];
                return (
                  <article
                    key={hook.id}
                    className="rounded-xl border-2 border-ink bg-card p-3 panel-shadow"
                  >
                    <p className="font-hand text-lg leading-snug">
                      {hookTitle(hook, gameState.cards)}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5 font-hand text-sm">
                      <span className="rounded-full border-[1.5px] border-ink bg-panel-paper px-2 py-0.5">
                        {hook.scope === "player"
                          ? `Affects ${owner?.name ?? "one player"}`
                          : "Affects everyone"}
                      </span>
                      {(hook.condition_keys ?? []).map((key) => (
                        <span
                          key={key}
                          className="rounded-full border-[1.5px] border-primary bg-primary/10 px-2 py-0.5 text-primary"
                        >
                          Affects: {conditionName(key)}
                        </span>
                      ))}
                    </div>
                    {source?.title && (
                      <p className="mt-2 font-hand text-sm text-muted-foreground">
                        From {source.title}
                      </p>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </OverlayShell>
  );
}
