"use client";

import { useMemo, useState, type ReactNode } from "react";
import {
  ArrowLeftIcon,
  CheckIcon,
  GavelIcon,
  MinusIcon,
  PlusIcon,
  ShuffleIcon,
  Trash2Icon,
} from "lucide-react";
import {
  OverlayShell,
  type PanelPresentation,
} from "@/components/overlay-shell";
import { PlayerAvatar } from "@/components/player-avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { conditionName, conditionValueDetail } from "@/lib/conditions";
import { playerColor } from "@/lib/players";
import type { AdminAction, ClientMsg, GameStateSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

type View = "main" | "move" | "condition" | "hooks" | "outcome" | "review";

interface HostControlOverlayProps {
  gameState: GameStateSnapshot;
  send: (message: ClientMsg) => void;
  presentation?: PanelPresentation;
  godMode?: boolean;
  godModeLoading?: boolean;
  onClose: () => void;
}

function cardTitle(state: GameStateSnapshot, cardId: string) {
  return state.cards[cardId]?.title || "Untitled card";
}

const CONDITION_KEY_ALIASES: Record<string, string> = {
  "skip next turn": "skip_next",
  "extra turn": "extra_turn",
};

function conditionKeyFromName(name: string): string {
  const normalizedName = name.trim().toLowerCase();
  const alias = CONDITION_KEY_ALIASES[normalizedName];
  if (alias) return alias;
  return normalizedName
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 80);
}

function conditionDisplay(key: string, value: unknown): string {
  const detail = conditionValueDetail(value);
  return `${conditionName(key)}${detail ? ` · ${detail}` : ""}`;
}

function actionLabel(action: AdminAction, state: GameStateSnapshot): string {
  const player = (id: string) =>
    state.players.find((candidate) => candidate.id === id)?.name ?? id;
  switch (action.kind) {
    case "set_score":
      return `Set ${player(action.player_id)}’s score to ${action.score}`;
    case "move_card":
      return `Move ${
        action.card_id
          ? cardTitle(state, action.card_id)
          : `${action.selector} deck card`
      }${
        action.source_zone === "hand" && action.source_player_id
          ? ` from ${player(action.source_player_id)}’s hand`
          : ""
      } to ${action.to_zone}`;
    case "shuffle_deck":
      return action.include_discard
        ? "Shuffle discard pile into the deck"
        : "Shuffle the deck";
    case "set_condition":
      return `Set ${player(action.player_id)}: ${conditionDisplay(action.key, action.value)}`;
    case "remove_condition":
      return `Remove ${action.key.replace(/_/g, " ")} from ${player(action.player_id)}`;
    case "remove_hook":
      return `Remove hook ${action.hook_id}`;
    case "eliminate_players":
      return `Mark ${action.player_ids.map(player).join(", ")} as loser(s)`;
    case "end_game":
      return action.winner_ids?.length
        ? `End game with ${action.winner_ids.map(player).join(", ")} as winner(s)`
        : "End game using the current win condition";
    case "set_result_winners":
      return action.winner_ids.length
        ? `Set final winner(s): ${action.winner_ids.map(player).join(", ")}`
        : "Set final result: no winner";
  }
}

export function HostControlOverlay({
  gameState,
  send,
  presentation = "modal",
  godMode = false,
  godModeLoading = false,
  onClose,
}: HostControlOverlayProps) {
  const [view, setView] = useState<View>("main");
  const [queued, setQueued] = useState<AdminAction[]>([]);
  const [scoreEdits, setScoreEdits] = useState<Record<string, string>>({});
  const [resultWinners, setResultWinners] = useState<string[]>(
    gameState.winner_ids ?? [],
  );

  const updateScore = (playerId: string, value: string) => {
    setScoreEdits((current) => ({ ...current, [playerId]: value }));
  };

  const scoreActions = useMemo<AdminAction[]>(
    () =>
      gameState.players.flatMap((player) => {
        if (scoreEdits[player.id] === undefined) return [];
        const value = Number(scoreEdits[player.id]);
        if (
          !Number.isInteger(value) ||
          value === player.score ||
          value < -1_000_000 ||
          value > 1_000_000
        )
          return [];
        return [{ kind: "set_score", player_id: player.id, score: value }];
      }),
    [gameState.players, scoreEdits],
  );
  const resultAction: AdminAction[] =
    gameState.phase === "results"
      ? [{ kind: "set_result_winners", winner_ids: resultWinners }]
      : [];
  const actions = [...scoreActions, ...queued, ...resultAction];

  const addAction = (action: AdminAction) => {
    setQueued((current) => {
      const withoutTerminal = current.filter(
        (queuedAction) => queuedAction.kind !== "end_game",
      );
      const next =
        action.kind === "end_game"
          ? [...withoutTerminal, action]
          : current.some((queuedAction) => queuedAction.kind === "end_game")
            ? [
                ...withoutTerminal,
                action,
                current.find(
                  (queuedAction) => queuedAction.kind === "end_game",
                )!,
              ]
            : [...current, action];
      return next.length + scoreActions.length + resultAction.length <= 20
        ? next
        : current;
    });
    setView("main");
  };

  const submit = () => {
    if (!actions.length || actions.length > 20) return;
    send({ type: "admin_propose", actions });
    setQueued([]);
    onClose();
  };

  if (godModeLoading) {
    return (
      <OverlayShell
        scrimTestId="host-controls-scrim"
        title="Host controls"
        subtitle="Opening protected God mode…"
        closeLabel="Close host controls"
        onClose={onClose}
        presentation={presentation}
        panelClassName="max-w-[760px]"
      >
        <p role="status" className="font-hand text-lg text-muted-foreground">
          Loading hands and deck order…
        </p>
      </OverlayShell>
    );
  }

  return (
    <OverlayShell
      scrimTestId="host-controls-scrim"
      title={
        gameState.phase === "results" ? "Correct results" : "Host controls"
      }
      subtitle={
        godMode
          ? "God mode · hidden card state is visible only in this panel"
          : "Every change needs unanimous table approval"
      }
      closeLabel="Close host controls"
      onClose={onClose}
      presentation={presentation}
      panelClassName="max-w-[760px]"
    >
      {view !== "main" && (
        <Button
          variant="ghost"
          className="mb-3 min-h-11"
          onClick={() => setView("main")}
        >
          <ArrowLeftIcon /> Back to controls
        </Button>
      )}

      {view === "main" && (
        <div className="flex flex-col gap-5">
          <section>
            <h3 className="font-marker text-lg">Points</h3>
            <p className="font-hand text-sm text-muted-foreground">
              Set the score the table should show.
            </p>
            <div className="mt-2 flex flex-col gap-2">
              {gameState.players.map((player, index) => {
                const parsed = Number(
                  scoreEdits[player.id] ?? String(player.score),
                );
                return (
                  <div
                    key={player.id}
                    className="flex items-center gap-2 rounded-xl border-2 border-ink bg-panel-paper p-2"
                  >
                    <PlayerAvatar
                      name={player.name}
                      color={playerColor(index)}
                      size={34}
                    />
                    <span className="min-w-0 flex-1 truncate font-hand text-lg">
                      {player.name}
                    </span>
                    <Button
                      variant="outline"
                      size="icon"
                      className="size-11 shrink-0"
                      aria-label={`Subtract one point from ${player.name}`}
                      onClick={() =>
                        updateScore(
                          player.id,
                          String(
                            (Number.isFinite(parsed) ? parsed : player.score) -
                              1,
                          ),
                        )
                      }
                    >
                      <MinusIcon />
                    </Button>
                    <Input
                      aria-label={`${player.name} score`}
                      type="number"
                      value={scoreEdits[player.id] ?? String(player.score)}
                      className="h-11 w-20 text-center font-mono"
                      onChange={(event) =>
                        updateScore(player.id, event.target.value)
                      }
                    />
                    <Button
                      variant="outline"
                      size="icon"
                      className="size-11 shrink-0"
                      aria-label={`Add one point to ${player.name}`}
                      onClick={() =>
                        updateScore(
                          player.id,
                          String(
                            (Number.isFinite(parsed) ? parsed : player.score) +
                              1,
                          ),
                        )
                      }
                    >
                      <PlusIcon />
                    </Button>
                  </div>
                );
              })}
            </div>
          </section>

          {gameState.phase === "results" ? (
            <WinnerPicker
              players={gameState.players}
              selected={resultWinners}
              onChange={setResultWinners}
              allowEmpty
            />
          ) : (
            <section>
              <h3 className="font-marker text-lg">Table state</h3>
              <div
                className={cn(
                  "mt-2 grid grid-cols-2 gap-2",
                  presentation === "modal" && "sm:grid-cols-3",
                )}
              >
                <ControlButton
                  label="Move a card"
                  onClick={() => setView("move")}
                />
                <ControlButton
                  label="Conditions"
                  onClick={() => setView("condition")}
                />
                <ControlButton label="Hooks" onClick={() => setView("hooks")} />
                <ControlButton
                  label="Shuffle deck"
                  icon={<ShuffleIcon />}
                  onClick={() =>
                    addAction({ kind: "shuffle_deck", include_discard: false })
                  }
                />
                <ControlButton
                  label="Reshuffle discard"
                  icon={<ShuffleIcon />}
                  disabled={gameState.discard.length === 0}
                  onClick={() =>
                    addAction({ kind: "shuffle_deck", include_discard: true })
                  }
                />
                <ControlButton
                  label="Game outcome"
                  icon={<GavelIcon />}
                  onClick={() => setView("outcome")}
                />
              </div>
            </section>
          )}

          {queued.length > 0 && (
            <section className="rounded-xl border-2 border-dashed border-ink p-3">
              <p className="font-marker text-sm">
                Added corrections ({queued.length})
              </p>
              <ul className="mt-1 list-disc pl-5 font-hand">
                {queued.map((action, index) => (
                  <li key={`${action.kind}-${index}`}>
                    {actionLabel(action, gameState)}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <Button
            size="lg"
            className="sticky bottom-0 min-h-12 w-full font-marker text-lg"
            disabled={
              actions.length === 0 ||
              actions.length > 20 ||
              (gameState.phase === "results" &&
                scoreActions.length === 0 &&
                JSON.stringify(resultWinners) ===
                  JSON.stringify(gameState.winner_ids ?? []))
            }
            onClick={() => setView("review")}
          >
            Review proposal ({actions.length})
          </Button>
        </div>
      )}

      {view === "move" && (
        <MoveCardForm
          gameState={gameState}
          godMode={godMode}
          onAdd={addAction}
        />
      )}
      {view === "condition" && (
        <ConditionForm gameState={gameState} onAdd={addAction} />
      )}
      {view === "hooks" && <HookList gameState={gameState} onAdd={addAction} />}
      {view === "outcome" && (
        <OutcomeForm gameState={gameState} onAdd={addAction} />
      )}
      {view === "review" && (
        <section>
          <h3 className="font-marker text-xl">Review the whole proposal</h3>
          <p className="mt-1 font-hand text-muted-foreground">
            The server will verify this list and show every player the exact
            before-and-after summary.
          </p>
          <ol className="mt-4 flex flex-col gap-2">
            {actions.map((action, index) => (
              <li
                key={`${action.kind}-${index}`}
                className="rounded-xl border-2 border-ink bg-panel-paper p-3 font-hand text-lg"
              >
                <span className="mr-2 font-mono text-sm">{index + 1}.</span>
                {actionLabel(action, gameState)}
              </li>
            ))}
          </ol>
          <Button
            size="lg"
            className="mt-5 min-h-12 w-full bg-marker-green font-marker text-lg text-white hover:bg-marker-green/90"
            onClick={submit}
          >
            <CheckIcon /> Propose changes
          </Button>
        </section>
      )}
    </OverlayShell>
  );
}

function ControlButton({
  label,
  onClick,
  icon,
  disabled,
}: {
  label: string;
  onClick: () => void;
  icon?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <Button
      variant="outline"
      className="min-h-16 whitespace-normal font-hand text-base"
      disabled={disabled}
      onClick={onClick}
    >
      {icon}
      {label}
    </Button>
  );
}

function MoveCardForm({
  gameState,
  godMode,
  onAdd,
}: {
  gameState: GameStateSnapshot;
  godMode: boolean;
  onAdd: (action: AdminAction) => void;
}) {
  const [sourceZone, setSourceZone] = useState<
    "deck" | "discard" | "center" | "exile" | "in_play" | "hand"
  >("discard");
  const [sourcePlayer, setSourcePlayer] = useState(
    gameState.players[0]?.id ?? "",
  );
  const [sourceValue, setSourceValue] = useState("");
  const [selector, setSelector] = useState<"top" | "bottom">("top");
  const [toZone, setToZone] = useState<
    "deck" | "discard" | "center" | "exile" | "in_play" | "hand"
  >("center");
  const [toPlayer, setToPlayer] = useState(gameState.players[0]?.id ?? "");
  const [deckPosition, setDeckPosition] = useState<
    "top" | "bottom" | "shuffle"
  >("top");

  const choices = useMemo(() => {
    if (sourceZone === "deck" && godMode)
      return gameState.deck.map((id, index) => ({
        id,
        playerId: "",
        label: `#${index + 1}${index === 0 ? " (top)" : index === gameState.deck.length - 1 ? " (bottom)" : ""} — ${cardTitle(gameState, id)}`,
      }));
    if (sourceZone === "hand") {
      const player = gameState.players.find(
        (candidate) => candidate.id === sourcePlayer,
      );
      return (player?.hand ?? []).map((id) => ({
        id,
        playerId: player?.id ?? "",
        label: cardTitle(gameState, id),
      }));
    }
    if (sourceZone === "discard")
      return gameState.discard.map((id) => ({
        id,
        playerId: "",
        label: cardTitle(gameState, id),
      }));
    if (sourceZone === "center")
      return gameState.house_rules.map((id) => ({
        id,
        playerId: "",
        label: cardTitle(gameState, id),
      }));
    if (sourceZone === "exile")
      return (gameState.exiled ?? []).map((id) => ({
        id,
        playerId: "",
        label: cardTitle(gameState, id),
      }));
    if (sourceZone === "in_play")
      return gameState.players.flatMap((player) =>
        player.in_play.map((id) => ({
          id,
          playerId: player.id,
          label: `${player.name} — ${cardTitle(gameState, id)}`,
        })),
      );
    return [];
  }, [gameState, godMode, sourcePlayer, sourceZone]);

  const add = () => {
    const selected = choices.find(
      (choice) => `${choice.playerId}|${choice.id}` === sourceValue,
    );
    if ((sourceZone !== "deck" || godMode) && !selected) return;
    onAdd({
      kind: "move_card",
      source_zone: sourceZone,
      ...(sourceZone === "deck"
        ? godMode
          ? { card_id: selected?.id }
          : { selector }
        : {
            card_id: selected?.id,
            ...(sourceZone === "in_play" || sourceZone === "hand"
              ? { source_player_id: selected?.playerId }
              : {}),
          }),
      to_zone: toZone,
      ...(toZone === "hand" || toZone === "in_play"
        ? { to_player_id: toPlayer }
        : {}),
      ...(toZone === "deck" ? { deck_position: deckPosition } : {}),
    });
  };

  return (
    <section className="flex flex-col gap-4">
      <h3 className="font-marker text-xl">Move a card</h3>
      <LabeledSelect
        label="From"
        value={sourceZone}
        onChange={(value) => {
          setSourceZone(value as typeof sourceZone);
          setSourceValue("");
        }}
        options={[
          ["discard", `Discard (${gameState.discard.length})`],
          ["center", `Center (${gameState.house_rules.length})`],
          ["exile", `Exile (${gameState.exiled?.length ?? 0})`],
          ["in_play", "Player in-play zones"],
          ...(godMode ? ([["hand", "Player hands"]] as string[][]) : []),
          [
            "deck",
            godMode
              ? `Ordered deck (${gameState.deck.length})`
              : `Hidden deck (${gameState.deck_count ?? 0})`,
          ],
        ]}
      />
      {sourceZone === "hand" && (
        <LabeledSelect
          label="Source player"
          value={sourcePlayer}
          onChange={(value) => {
            setSourcePlayer(value);
            setSourceValue("");
          }}
          options={gameState.players.map((player) => [player.id, player.name])}
        />
      )}
      {sourceZone === "deck" && !godMode ? (
        <LabeledSelect
          label="Hidden card"
          value={selector}
          onChange={(value) => setSelector(value as typeof selector)}
          options={[
            ["top", "Top card"],
            ["bottom", "Bottom card"],
          ]}
        />
      ) : (
        <LabeledSelect
          label={sourceZone === "deck" ? "Exact deck card" : "Card"}
          value={sourceValue}
          onChange={setSourceValue}
          options={[
            ["", choices.length ? "Choose a card" : "No cards in this zone"],
            ...choices.map((choice) => [
              `${choice.playerId}|${choice.id}`,
              choice.label,
            ]),
          ]}
        />
      )}
      <LabeledSelect
        label="To"
        value={toZone}
        onChange={(value) => setToZone(value as typeof toZone)}
        options={[
          ["center", "Center"],
          ["in_play", "Player in-play zone"],
          ["hand", "Player hand"],
          ["discard", "Discard"],
          ["exile", "Exile"],
          ["deck", "Deck"],
        ]}
      />
      {(toZone === "hand" || toZone === "in_play") && (
        <LabeledSelect
          label="Player"
          value={toPlayer}
          onChange={setToPlayer}
          options={gameState.players.map((player) => [player.id, player.name])}
        />
      )}
      {toZone === "deck" && (
        <LabeledSelect
          label="Deck position"
          value={deckPosition}
          onChange={(value) => setDeckPosition(value as typeof deckPosition)}
          options={[
            ["top", "Top"],
            ["bottom", "Bottom"],
            ["shuffle", "Random position"],
          ]}
        />
      )}
      <Button
        size="lg"
        className="min-h-12"
        disabled={
          (sourceZone === "deck" && (gameState.deck_count ?? 0) === 0) ||
          ((sourceZone !== "deck" || godMode) && !sourceValue)
        }
        onClick={add}
      >
        Add card move
      </Button>
    </section>
  );
}

function ConditionForm({
  gameState,
  onAdd,
}: {
  gameState: GameStateSnapshot;
  onAdd: (action: AdminAction) => void;
}) {
  const [playerId, setPlayerId] = useState(gameState.players[0]?.id ?? "");
  const [conditionName, setConditionName] = useState("");
  const [value, setValue] = useState("true");
  const [valueType, setValueType] = useState<"boolean" | "number" | "text">(
    "boolean",
  );
  const [duration, setDuration] = useState("");
  const player = gameState.players.find(
    (candidate) => candidate.id === playerId,
  );

  const add = () => {
    const key = conditionKeyFromName(conditionName);
    if (!key) return;
    let parsed: string | number | boolean = value;
    if (valueType === "boolean") parsed = true;
    if (valueType === "number") parsed = Number(value);
    if (
      (valueType === "number" && !Number.isFinite(parsed)) ||
      (valueType !== "boolean" && !value.trim())
    )
      return;
    onAdd({
      kind: "set_condition",
      player_id: playerId,
      key,
      value: parsed,
      ...(duration ? { duration_turns: Number(duration) } : {}),
    });
  };

  return (
    <section className="flex flex-col gap-4">
      <h3 className="font-marker text-xl">Conditions</h3>
      <LabeledSelect
        label="Player"
        value={playerId}
        onChange={setPlayerId}
        options={gameState.players.map((candidate) => [
          candidate.id,
          candidate.name,
        ])}
      />
      {player && Object.keys(player.conditions).length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="font-hand text-sm text-muted-foreground">
            Current conditions
          </p>
          {Object.entries(player.conditions).map(([conditionKey, current]) => (
            <div
              key={conditionKey}
              className="flex items-center gap-2 rounded-xl border-2 border-ink bg-panel-paper p-2"
            >
              <span className="min-w-0 flex-1 font-hand">
                {conditionDisplay(conditionKey, current)}
              </span>
              <Button
                variant="outline"
                size="icon"
                className="size-11 border-destructive text-destructive"
                aria-label={`Remove ${conditionKey}`}
                onClick={() =>
                  onAdd({
                    kind: "remove_condition",
                    player_id: playerId,
                    key: conditionKey,
                  })
                }
              >
                <Trash2Icon />
              </Button>
            </div>
          ))}
        </div>
      )}
      <label className="flex flex-col gap-1 font-hand text-base">
        <span className="text-muted-foreground">Condition</span>
        <Input
          aria-label="Condition name"
          placeholder="e.g. Left hand only or Speak only in questions"
          value={conditionName}
          maxLength={120}
          onChange={(event) => setConditionName(event.target.value)}
        />
        <span className="text-sm text-muted-foreground">
          Enter any condition your table agreed to. It will appear on this
          player’s seat.
        </span>
      </label>
      <LabeledSelect
        label="How should it be tracked?"
        value={valueType}
        onChange={(next) => {
          const type = next as typeof valueType;
          setValueType(type);
          setValue(type === "boolean" ? "true" : "");
        }}
        options={[
          ["boolean", "On / off (most conditions)"],
          ["number", "Number or stacks"],
          ["text", "Text note"],
        ]}
      />
      {valueType !== "boolean" && (
        <Input
          aria-label="Condition value"
          type={valueType === "number" ? "number" : "text"}
          placeholder={
            valueType === "number"
              ? "Number of stacks, e.g. 3"
              : "Short note shown with the condition"
          }
          value={value}
          maxLength={500}
          onChange={(event) => setValue(event.target.value)}
        />
      )}
      <Input
        type="number"
        min={1}
        max={1000}
        placeholder="Duration in this player’s turns (optional)"
        value={duration}
        onChange={(event) => setDuration(event.target.value)}
      />
      <Button
        size="lg"
        className="min-h-12"
        disabled={
          !conditionKeyFromName(conditionName) ||
          (valueType !== "boolean" && !value.trim())
        }
        onClick={add}
      >
        Add condition change
      </Button>
    </section>
  );
}

function HookList({
  gameState,
  onAdd,
}: {
  gameState: GameStateSnapshot;
  onAdd: (action: AdminAction) => void;
}) {
  return (
    <section>
      <h3 className="font-marker text-xl">Active hooks</h3>
      <div className="mt-3 flex flex-col gap-2">
        {gameState.hooks.length === 0 ? (
          <p className="font-hand text-lg italic text-muted-foreground">
            No active hooks.
          </p>
        ) : (
          gameState.hooks.map((hook) => (
            <div
              key={hook.id}
              className="flex items-center gap-2 rounded-xl border-2 border-ink bg-panel-paper p-3"
            >
              <div className="min-w-0 flex-1">
                <p className="font-marker text-sm">
                  {cardTitle(gameState, hook.source_card_id)}
                </p>
                <p className="font-hand">
                  {hook.event} · {hook.scope}
                </p>
              </div>
              <Button
                variant="outline"
                className="min-h-11 border-destructive text-destructive"
                onClick={() => onAdd({ kind: "remove_hook", hook_id: hook.id })}
              >
                Remove
              </Button>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function OutcomeForm({
  gameState,
  onAdd,
}: {
  gameState: GameStateSnapshot;
  onAdd: (action: AdminAction) => void;
}) {
  const [losers, setLosers] = useState<string[]>([]);
  const [winners, setWinners] = useState<string[]>([]);
  return (
    <section className="flex flex-col gap-5">
      <WinnerPicker
        title="Mark loser(s)"
        players={gameState.players.filter((player) => !player.eliminated)}
        selected={losers}
        onChange={setLosers}
      />
      <Button
        variant="outline"
        className="min-h-11 border-destructive text-destructive"
        disabled={!losers.length}
        onClick={() => onAdd({ kind: "eliminate_players", player_ids: losers })}
      >
        Add loser elimination
      </Button>
      <WinnerPicker
        title="Declare winner(s) and end"
        players={gameState.players.filter((player) => !player.eliminated)}
        selected={winners}
        onChange={setWinners}
      />
      <Button
        className="min-h-11"
        disabled={!winners.length}
        onClick={() => onAdd({ kind: "end_game", winner_ids: winners })}
      >
        Add declared winner(s)
      </Button>
      <Button
        variant="outline"
        className="min-h-11"
        onClick={() => onAdd({ kind: "end_game" })}
      >
        End using current win condition
      </Button>
    </section>
  );
}

function WinnerPicker({
  players,
  selected,
  onChange,
  title = "Final winner(s)",
  allowEmpty = false,
}: {
  players: GameStateSnapshot["players"];
  selected: string[];
  onChange: (ids: string[]) => void;
  title?: string;
  allowEmpty?: boolean;
}) {
  const toggle = (playerId: string) =>
    onChange(
      selected.includes(playerId)
        ? selected.filter((id) => id !== playerId)
        : [...selected, playerId],
    );
  return (
    <section>
      <h3 className="font-marker text-lg">{title}</h3>
      {allowEmpty && (
        <p className="font-hand text-sm text-muted-foreground">
          Leave everyone unselected for no winner.
        </p>
      )}
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
        {players.map((player) => (
          <button
            type="button"
            key={player.id}
            aria-pressed={selected.includes(player.id)}
            className={`min-h-12 rounded-xl border-2 p-2 font-hand text-lg ${
              selected.includes(player.id)
                ? "border-marker-green bg-marker-green/10"
                : "border-ink bg-card"
            }`}
            onClick={() => toggle(player.id)}
          >
            {player.name}
          </button>
        ))}
      </div>
    </section>
  );
}

function LabeledSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: string[][];
}) {
  return (
    <label className="flex flex-col gap-1 font-hand text-base">
      <span className="text-muted-foreground">{label}</span>
      <select
        value={value}
        className="min-h-11 w-full rounded-lg border-2 border-ink bg-card px-3 font-hand text-lg"
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={`${optionValue}-${optionLabel}`} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </label>
  );
}
