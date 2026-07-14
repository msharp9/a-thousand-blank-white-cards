"use client";

import { useCallback, useMemo, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { DiscardPile } from "@/components/discard-pile";
import { EffectLog } from "@/components/effect-log";
import { DynamicStatePanel } from "@/components/dynamic-state-panel";
import { EpilogueView } from "@/components/epilogue";
import { GameNavTabs } from "@/components/game-nav-tabs";
import { GameTable } from "@/components/game-table";
import { Hand } from "@/components/hand";
import { HistoryModal } from "@/components/history-modal";
import { HouseRulesZone } from "@/components/house-rules-zone";
import { InteractionPanel } from "@/components/interaction-panel";
import { FeltDropZone, PlayDndContext } from "@/components/play-dnd";
import { ReactionWindow } from "@/components/reaction-window";
import { PlayerAvatar } from "@/components/player-avatar";
import { ResultsScreen } from "@/components/results-screen";
import { SetupPhase } from "@/components/setup-phase";
import { SketchCard, stableRotation } from "@/components/sketch-card";
import { getCardArtUrl } from "@/lib/art";
import { interactionResponseMessage } from "@/lib/interactions";
import { playerColor } from "@/lib/players";
import type {
  CardSnapshot,
  GameStateSnapshot,
  PromptChoiceMsg,
} from "@/lib/types";
import { getPlayerId, storePlayerId, useGameSocket } from "@/lib/ws";
import { cn } from "@/lib/utils";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const PHASE_LABELS: Record<GameStateSnapshot["phase"], string> = {
  lobby: "Lobby",
  setup: "Setup",
  playing: "Playing",
  results: "Results",
  epilogue: "Epilogue",
  ended: "Ended",
};

export default function RoomPage() {
  const params = useParams();
  const router = useRouter();
  const code = ((params.code as string) ?? "").toUpperCase();

  const subscribeStorage = useCallback((onChange: () => void) => {
    window.addEventListener("storage", onChange);
    return () => window.removeEventListener("storage", onChange);
  }, []);

  // Player identity is written per-room to sessionStorage by the landing page
  // before navigation. useSyncExternalStore is the SSR-safe way to read it:
  // the server snapshot is null (matching the pre-hydration markup) and the
  // real value is adopted after hydration without a mismatch warning.
  const myPlayerId = useSyncExternalStore(
    subscribeStorage,
    () => getPlayerId(code),
    () => null,
  );
  const storedName = useSyncExternalStore(
    subscribeStorage,
    () => localStorage.getItem("tbwc_player_name"),
    () => null,
  );

  const [name, setName] = useState("");
  const [nameSet, setNameSet] = useState(false);
  const [adoptedStoredName, setAdoptedStoredName] = useState(false);
  const [joining, setJoining] = useState(false);
  const [joinError, setJoinError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  // Once the stored name hydrates in, adopt it and skip the name gate.
  // Adjusting state during render is React's recommended alternative to a
  // hydration effect and avoids a cascading re-render.
  if (!adoptedStoredName && storedName) {
    setAdoptedStoredName(true);
    setName(storedName);
    setNameSet(true);
  }

  // Direct-paste entry: unlike the landing page, a user opening /room/{code}
  // directly never did the REST join, so there is no player_id in
  // sessionStorage and the WS join would be rejected. Do the same POST the
  // landing page does (persist name + store player_id) before opening the WS.
  const handleJoin = useCallback(async () => {
    const trimmed = name.trim();
    if (!trimmed || joining) return;
    setJoining(true);
    setJoinError(null);
    try {
      const joinRes = await fetch(`${API_URL}/rooms/${code}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      });
      if (!joinRes.ok)
        throw new Error(
          joinRes.status === 404 ? "Room not found" : "Failed to join",
        );
      const { player_id } = await joinRes.json();
      storePlayerId(code, player_id);
      localStorage.setItem("tbwc_player_name", trimmed);
      setNameSet(true);
    } catch (e) {
      setJoinError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setJoining(false);
    }
  }, [code, name, joining]);

  const {
    gameState,
    log,
    brewing,
    previewResult,
    fatalError,
    transientError,
    clearTransientError,
    connected,
    promptChoice,
    clearPromptChoice,
    epilogueCards,
    interactionRequest,
    interactionProgress,
    reactionResult,
    send,
  } = useGameSocket(nameSet ? code : "", name);

  const phase = gameState?.phase ?? "lobby";

  const me = gameState?.players.find((p) => p.id === myPlayerId);
  const myIndex =
    gameState?.players.findIndex((p) => p.id === myPlayerId) ?? -1;
  // A spectator joined after the game started: they observe but can't act, so
  // all play/pass/author controls are hidden and a banner is shown instead.
  // Spectators live in their own snapshot collection, not `players`.
  const isSpectator = Boolean(
    gameState?.spectators.some((s) => s.id === myPlayerId),
  );
  const isActive = useMemo(() => {
    if (!gameState || !gameState.players.length || !myPlayerId) return false;
    if (isSpectator) return false;
    const active =
      gameState.players[gameState.turn_index % gameState.players.length];
    return active?.id === myPlayerId;
  }, [gameState, myPlayerId, isSpectator]);

  const myHandCards: CardSnapshot[] = useMemo(() => {
    if (!gameState || !me) return [];
    return me.hand
      .map((id) => gameState.cards[id])
      .filter((c): c is CardSnapshot => Boolean(c));
  }, [gameState, me]);

  const myInPlayCards: CardSnapshot[] = useMemo(() => {
    if (!gameState || !me) return [];
    return (me.in_play ?? [])
      .map((id) => gameState.cards[id])
      .filter((c): c is CardSnapshot => Boolean(c));
  }, [gameState, me]);

  const houseRuleCards: CardSnapshot[] = useMemo(() => {
    if (!gameState) return [];
    return gameState.house_rules
      .map((id) => gameState.cards[id])
      .filter((c): c is CardSnapshot => Boolean(c));
  }, [gameState]);

  const isHost = Boolean(
    gameState && myPlayerId && gameState.players[0]?.id === myPlayerId,
  );

  // Winner names for the epilogue banner: the backend resolves scoring and sets
  // winner_ids at the playing → epilogue transition, so they're known before
  // voting. Empty when there's no winner (banner is hidden).
  const epilogueWinnerNames: string[] = useMemo(() => {
    if (!gameState) return [];
    const ids = gameState.winner_ids ?? [];
    return gameState.players
      .filter((p) => ids.includes(p.id))
      .map((p) => p.name);
  }, [gameState]);

  // The most recent discard, shown as the dock's discard indicator.
  const topDiscard: CardSnapshot | undefined = gameState
    ? gameState.cards[gameState.discard[gameState.discard.length - 1] ?? ""]
    : undefined;

  // Open reaction window (a play suspended while others may counter it). The
  // snapshot's pending_play is the source of truth; each client derives its
  // own eligibility from the reaction cards in its hand.
  const pendingPlay =
    phase === "playing" ? (gameState?.pending_play ?? null) : null;
  const pendingCard = pendingPlay
    ? gameState?.cards[pendingPlay.card_id]
    : undefined;
  const pendingActorName =
    gameState?.players.find((p) => p.id === pendingPlay?.actor_id)?.name ??
    "Someone";
  const myReactionCards = useMemo(
    () => myHandCards.filter((c) => c.canonical?.trigger === "on_reaction"),
    [myHandCards],
  );
  const reactionResultText = useMemo(() => {
    if (!reactionResult || reactionResult.outcome === "resolved") return null;
    const reactor =
      gameState?.players.find((p) => p.id === reactionResult.reactor_id)
        ?.name ?? "Someone";
    switch (reactionResult.outcome) {
      case "countered":
        return `💥 Countered by ${reactor}!`;
      case "stolen":
        return `🫳 ${reactor} stole the card!`;
      case "redirected":
        return `↩️ ${reactor} redirected it!`;
      default:
        return null;
    }
  }, [reactionResult, gameState]);

  // ── name gate ──
  if (!nameSet) {
    return (
      <main className="flex h-dvh flex-col items-center justify-center p-4">
        <div className="flex w-full max-w-sm -rotate-[0.5deg] flex-col items-center gap-4 rounded-2xl border-[2.5px] border-ink bg-card p-6 panel-shadow">
          <p className="text-center font-hand text-xl">
            Enter your name to join room{" "}
            <span className="font-mono text-lg">{code}</span>
          </p>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            maxLength={24}
            className="max-w-xs font-hand text-lg"
            onKeyDown={(e) => e.key === "Enter" && handleJoin()}
          />
          <Button disabled={!name.trim() || joining} onClick={handleJoin}>
            {joining ? "Joining…" : "Enter"}
          </Button>
          {joinError && (
            <p className="font-hand text-base text-destructive">{joinError}</p>
          )}
        </div>
      </main>
    );
  }

  // Surface a fatal join/WS rejection before the "Connecting" spinner so a hard
  // rejection (e.g. server closes with 4001) is shown instead of spinning
  // forever. Recoverable, message-level errors do NOT come through here — they
  // render as a transient banner over the live game (see below).
  if (fatalError) {
    return (
      <main className="flex h-dvh flex-col items-center justify-center p-4">
        <div className="flex w-full max-w-sm rotate-[0.5deg] flex-col items-center gap-4 rounded-2xl border-[2.5px] border-ink bg-card p-6 panel-shadow">
          <p className="text-center font-hand text-xl text-destructive">
            {fatalError}
          </p>
          <Button variant="outline" onClick={() => router.push("/")}>
            Back to lobby
          </Button>
        </div>
      </main>
    );
  }

  if (!connected && !gameState) {
    return (
      <main className="flex h-dvh items-center justify-center p-4">
        <p className="-rotate-[0.5deg] rounded-2xl border-[2.5px] border-ink bg-card px-8 py-5 font-hand text-xl panel-shadow">
          Connecting to room <span className="font-mono text-lg">{code}</span>…
        </p>
      </main>
    );
  }

  return (
    <main className="flex h-dvh flex-col">
      {/* Recoverable, message-level errors (e.g. "Not your turn") show as a
          dismissible banner over the live game — the table stays mounted and
          interactive. Auto-clears from the socket layer. */}
      {transientError && (
        <div
          role="alert"
          className="fixed inset-x-0 top-3 z-50 mx-auto flex w-fit max-w-[calc(100%-2rem)] -rotate-[0.5deg] items-center gap-3 rounded-xl border-2 border-ink bg-card px-3 py-2 font-hand text-base text-destructive sticker-shadow-sm"
        >
          <span>{transientError}</span>
          <Button
            variant="ghost"
            size="icon-xs"
            className="text-destructive hover:bg-destructive/10"
            onClick={clearTransientError}
          >
            <XIcon />
            <span className="sr-only">Dismiss</span>
          </Button>
        </div>
      )}
      <InteractionPanel
        pending={gameState?.pending_interaction}
        request={interactionRequest}
        progressMessage={interactionProgress}
        cards={gameState?.cards ?? {}}
        onSubmit={(interactionId, payload) =>
          send(interactionResponseMessage(interactionId, payload))
        }
      />
      {pendingPlay && (
        <ReactionWindow
          pending={pendingPlay}
          pendingCard={pendingCard}
          actorName={pendingActorName}
          myReactionCards={myReactionCards}
          isActor={pendingPlay.actor_id === myPlayerId}
          isSpectator={isSpectator}
          send={send}
          roomCode={code}
        />
      )}
      {reactionResultText && (
        <div className="fixed inset-x-0 bottom-4 z-50 mx-auto w-fit rotate-[0.4deg] rounded-xl border-2 border-ink bg-card px-4 py-2 font-hand text-lg sticker-shadow-sm">
          {reactionResultText}
        </div>
      )}
      <header className="sticky top-0 z-40 flex items-center gap-3.5 border-b-[2.5px] border-ink bg-card px-5 py-2.5 shadow-[0_3px_0_rgba(26,26,26,0.08)]">
        <Link
          href="/"
          className="shrink-0 font-marker text-xl leading-[0.95] !text-ink"
        >
          1KBWC
        </Link>
        <span className="h-6 w-0.5 bg-ink/20" />
        <span className="font-mono text-sm text-muted-foreground">{code}</span>
        <span className="font-hand text-[17px] text-muted-foreground">
          {PHASE_LABELS[phase]}
        </span>
        {phase === "playing" && gameState && (
          <>
            <span className="font-hand text-[17px] text-muted-foreground">
              Turn {gameState.turn_number}
            </span>
            <GameNavTabs gameState={gameState} roomCode={code} />
          </>
        )}
        {isSpectator && (
          <span className="rounded-lg border-[1.5px] border-ink bg-panel-paper px-2 py-0.5 font-hand text-sm">
            Spectating
          </span>
        )}
        <span
          className="ml-auto flex items-center gap-1.5 font-hand text-sm text-muted-foreground"
          title={connected ? "Connected" : "Reconnecting…"}
        >
          <span
            className={cn(
              "size-2.5 rounded-full border border-ink",
              connected ? "bg-marker-green" : "animate-pulse bg-amber",
            )}
          />
          {connected ? "connected" : "reconnecting…"}
        </span>
      </header>

      <div className={cn("flex-1 overflow-auto", phase !== "playing" && "p-4")}>
        {!gameState && (
          <p className="p-4 font-hand text-lg text-muted-foreground">
            Waiting for game state…
          </p>
        )}

        {gameState && phase === "lobby" && (
          <div className="flex flex-col items-center pt-10">
            <div className="flex w-full max-w-sm -rotate-[0.6deg] flex-col items-center gap-4 rounded-2xl border-[2.5px] border-ink bg-card p-6 panel-shadow">
              <h2 className="font-marker text-2xl">The Lobby</h2>
              <p className="text-center font-hand text-lg text-muted-foreground">
                Waiting for players — share the room code{" "}
                <span className="font-mono text-base text-ink">{code}</span>.
              </p>
              {gameState.players.length > 0 && (
                <ul className="flex w-full flex-col gap-2">
                  {gameState.players.map((p, i) => (
                    <li key={p.id} className="flex items-center gap-2.5">
                      <PlayerAvatar
                        name={p.name}
                        color={playerColor(i)}
                        size={30}
                      />
                      <span className="font-hand text-lg">
                        {p.name}
                        {p.id === myPlayerId && " (you)"}
                        {i === 0 && (
                          <span className="ml-1 text-sm text-muted-foreground">
                            · host
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {isHost ? (
                <Button
                  size="lg"
                  className="font-marker text-lg"
                  onClick={() => send({ type: "start" })}
                >
                  Start game
                </Button>
              ) : (
                <p className="font-hand text-base italic text-muted-foreground">
                  Waiting for the host to start…
                </p>
              )}
            </div>
          </div>
        )}

        {gameState && phase === "setup" && (
          <SetupPhase
            gameState={gameState}
            myPlayerId={myPlayerId ?? ""}
            send={send}
            previewResult={previewResult}
            isSpectator={isSpectator}
          />
        )}

        {gameState && phase === "playing" && (
          /* Drag-and-drop card play: hand cards drag onto the felt (general
             play) or an opponent seat (targeted play). Click-to-select + Play
             keeps working unchanged inside <Hand>. */
          <PlayDndContext cards={gameState.cards} roomCode={code} send={send}>
            <div className="flex min-h-full flex-col">
              <GameTable gameState={gameState} myPlayerId={myPlayerId ?? ""} />

              {/* felt table: center zone + deck/action dock */}
              <FeltDropZone className="mx-4 my-2.5 flex min-h-[380px] flex-1 items-stretch overflow-hidden rounded-[22px] border-[3px] border-ink bg-felt shadow-[inset_0_0_60px_rgba(0,0,0,0.18)]">
                <HouseRulesZone
                  centerCards={houseRuleCards}
                  brewingCardId={brewing}
                  roomCode={code}
                />
                <div className="flex shrink-0 flex-col items-center justify-center gap-3.5 border-l-2 border-dashed border-white/30 bg-black/15 px-5 py-4">
                  <div className="text-center">
                    {gameState.deck.length > 0 ? (
                      <div className="relative mx-auto h-32 w-[92px]">
                        <SketchCard
                          faceDown
                          showTape={false}
                          w={92}
                          rot={3}
                          className="absolute top-1 left-1"
                        />
                        <SketchCard
                          faceDown
                          showTape={false}
                          w={92}
                          rot={-2}
                          className="absolute top-0.5 left-0.5"
                        />
                        <SketchCard
                          faceDown
                          showTape={false}
                          w={92}
                          className="absolute top-0 left-0"
                        />
                      </div>
                    ) : (
                      <div className="mx-auto flex h-32 w-[92px] items-center justify-center rounded-[7px] border-2 border-dashed border-white/40 font-hand text-sm text-white/70">
                        empty
                      </div>
                    )}
                    <p className="mt-1.5 font-hand text-[15px] text-white">
                      Deck · {gameState.deck.length}
                    </p>
                  </div>
                  <DiscardPile
                    topCard={topDiscard}
                    count={gameState.discard.length}
                    roomCode={code}
                    onClick={() => setHistoryOpen(true)}
                  />
                </div>
              </FeltDropZone>

              {/* your zone */}
              {isSpectator ? (
                <div className="border-t-[2.5px] border-ink bg-card px-5 py-4">
                  <p className="mx-auto w-fit rounded-xl border-2 border-dashed border-ink/40 px-5 py-3 font-hand text-base text-muted-foreground">
                    You joined after the game started — you are spectating and
                    cannot play cards.
                  </p>
                </div>
              ) : (
                <div className="border-t-[2.5px] border-ink bg-card px-5 pt-3 pb-4">
                  <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2.5">
                    <div className="flex items-center gap-2.5">
                      {me && (
                        <>
                          <PlayerAvatar
                            name={me.name}
                            color={playerColor(myIndex)}
                            size={38}
                          />
                          <span className="font-hand text-[22px] leading-none">
                            {me.name}
                            {isActive && (
                              <span className="ml-1 text-[15px] text-primary">
                                · your turn
                              </span>
                            )}
                          </span>
                          <span
                            className="font-marker text-2xl tabular-nums"
                            style={{ color: playerColor(myIndex) }}
                          >
                            {me.score}
                          </span>
                        </>
                      )}
                    </div>
                    {/* End turn only when the player may pass (holds no playable
                      card); the server drew for them at turn start. Hidden
                      while a play is brewing — the server freezes game
                      actions during interpretation. */}
                    {isActive && !brewing && gameState.can_pass && (
                      <Button
                        variant="outline"
                        onClick={() => send({ type: "pass" })}
                      >
                        End Turn ⟳
                      </Button>
                    )}
                  </div>
                  {myInPlayCards.length > 0 && (
                    <div className="mb-1 flex items-center gap-2">
                      <span className="font-hand text-sm text-muted-foreground">
                        In front of you:
                      </span>
                      {myInPlayCards.map((card) => (
                        <SketchCard
                          key={card.id}
                          card={card}
                          w={56}
                          showTape={false}
                          rot={stableRotation(card.id, 4)}
                          artUrl={getCardArtUrl(code, card)}
                        />
                      ))}
                    </div>
                  )}
                  <Hand
                    cards={myHandCards}
                    canPlay={isActive}
                    brewing={brewing}
                    send={send}
                    roomCode={code}
                  />
                </div>
              )}

              <EffectLog log={log} brewing={brewing} />
              <DynamicStatePanel gameState={gameState} />
            </div>
          </PlayDndContext>
        )}

        {gameState && phase === "results" && (
          <ResultsScreen
            gameState={gameState}
            myPlayerId={myPlayerId ?? ""}
            log={log}
            isHost={isHost}
            send={send}
            onBack={() => router.push("/")}
          />
        )}

        {gameState && phase === "epilogue" && (
          <div className="flex flex-col gap-4">
            {epilogueWinnerNames.length > 0 && (
              <div className="mx-auto -rotate-[0.5deg] rounded-xl border-2 border-ink bg-card px-5 py-2 text-center panel-shadow">
                <p className="font-hand text-lg">
                  {epilogueWinnerNames.length > 1 ? "Winners" : "Winner"}:{" "}
                  <span className="font-marker text-base text-primary">
                    {epilogueWinnerNames.join(", ")}
                  </span>
                </p>
              </div>
            )}
            <EpilogueView
              cards={epilogueCards}
              send={send}
              isHost={isHost}
              roomCode={code}
            />
          </div>
        )}

        {gameState && phase === "ended" && (
          <ResultsScreen
            gameState={gameState}
            myPlayerId={myPlayerId ?? ""}
            log={log}
            isHost={isHost}
            send={send}
            onBack={() => router.push("/")}
          />
        )}
      </div>

      <TargetPickerDialog
        prompt={promptChoice}
        playedTitle={
          promptChoice
            ? (gameState?.cards[promptChoice.card_id]?.title ?? "")
            : ""
        }
        players={gameState?.players ?? []}
        onPick={(choice) => {
          if (!promptChoice) return;
          // A prompt option carries either a player_id (player-target axis) or a
          // card_id (card-target axis). Re-send the play with the picked target;
          // the backend re-interprets, validates, applies, and advances.
          // While a reaction window is open, a prompt for any card other than
          // the suspended one is a REACTION needing a target — its follow-up
          // must re-carry as_reaction so it routes back into the window.
          const asReaction = Boolean(
            pendingPlay && promptChoice.card_id !== pendingPlay.card_id,
          );
          if (choice.player_id) {
            send({
              type: "play",
              card_id: promptChoice.card_id,
              chosen_player_id: choice.player_id,
              ...(asReaction ? { as_reaction: true } : {}),
            });
          } else if (choice.card_id) {
            send({
              type: "play",
              card_id: promptChoice.card_id,
              chosen_card_id: choice.card_id,
              ...(asReaction ? { as_reaction: true } : {}),
            });
          }
          clearPromptChoice();
        }}
        onCancel={clearPromptChoice}
      />

      {gameState && (
        <HistoryModal
          open={historyOpen}
          onOpenChange={setHistoryOpen}
          gameState={gameState}
          roomCode={code}
        />
      )}
    </main>
  );
}

// Renders the target picker when the server asks the active player to choose a
// target for the card they just played. Picking sends a follow-up play carrying
// the choice; cancelling abandons the pending play (the turn never advanced).
function TargetPickerDialog({
  prompt,
  playedTitle,
  players,
  onPick,
  onCancel,
}: {
  prompt: PromptChoiceMsg | null;
  playedTitle: string;
  players: { id: string }[];
  onPick: (choice: PromptChoiceMsg["choices"][number]) => void;
  onCancel: () => void;
}) {
  return (
    <Dialog
      open={Boolean(prompt)}
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
    >
      <DialogContent className="animate-popin border-2 border-dashed border-ink bg-panel-paper shadow-none">
        <DialogHeader>
          <DialogTitle className="font-hand text-xl font-normal">
            {playedTitle ? (
              <>
                Play <b>“{playedTitle}”</b> to:
              </>
            ) : (
              "Choose a target"
            )}
          </DialogTitle>
          {prompt && (
            <DialogDescription className="font-hand text-base">
              {prompt.prompt}
            </DialogDescription>
          )}
        </DialogHeader>
        <div className="flex flex-wrap items-center gap-2">
          {prompt?.choices.map((choice) => {
            const targetIndex = choice.player_id
              ? players.findIndex((p) => p.id === choice.player_id)
              : -1;
            return (
              <Button
                key={choice.player_id ?? choice.card_id}
                variant={targetIndex >= 0 ? "default" : "outline"}
                style={
                  targetIndex >= 0
                    ? { backgroundColor: playerColor(targetIndex) }
                    : undefined
                }
                onClick={() => onPick(choice)}
              >
                {choice.name}
              </Button>
            );
          })}
          <Button
            variant="ghost"
            className="text-muted-foreground"
            onClick={onCancel}
          >
            Cancel
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
