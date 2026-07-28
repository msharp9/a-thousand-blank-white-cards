"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { AdminProposalDialog } from "@/components/admin-proposal-dialog";
import { Input } from "@/components/ui/input";
import {
  CurrentTurnBadge,
  CurrentTurnIndicator,
} from "@/components/current-turn-indicator";
import { DiscardPile } from "@/components/discard-pile";
import { DynamicStatePanel } from "@/components/dynamic-state-panel";
import { EpilogueView } from "@/components/epilogue";
import { ExilePile } from "@/components/exile-pile";
import { GameNavTabs, type GameView } from "@/components/game-nav-tabs";
import { GameTable } from "@/components/game-table";
import { GameViewPanel } from "@/components/game-view-panel";
import { Hand } from "@/components/hand";
import { HandRevealDialog } from "@/components/hand-reveal-dialog";
import { HistoryModal } from "@/components/history-modal";
import { HostControlOverlay } from "@/components/host-control-overlay";
import { HouseRulesZone } from "@/components/house-rules-zone";
import { InteractionPanel } from "@/components/interaction-panel";
import { FeltDropZone, PlayDndContext } from "@/components/play-dnd";
import { ReactionWindow } from "@/components/reaction-window";
import { PlayerAvatar } from "@/components/player-avatar";
import { ResultsScreen } from "@/components/results-screen";
import { SetupPhase } from "@/components/setup-phase";
import { SketchCard, stableRotation } from "@/components/sketch-card";
import { TargetPickerDialog } from "@/components/target-picker-dialog";
import { TurnTimerChip } from "@/components/turn-timer";
import { ViewportNoticeHost } from "@/components/viewport-notice";
import { getCardArtUrl } from "@/lib/art";
import { interactionResponseMessage } from "@/lib/interactions";
import { playerColor } from "@/lib/players";
import {
  useCompactViewport,
  useWideGameView,
} from "@/lib/use-compact-viewport";
import type { CardSnapshot, ClientMsg, GameStateSnapshot } from "@/lib/types";
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
  const [resultsAdminOpen, setResultsAdminOpen] = useState(false);
  const [gameView, setGameView] = useState<GameView>("table");
  const compactViewport = useCompactViewport();
  const wideGameView = useWideGameView();

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
    adminGameState,
    clearAdminGameState,
    log,
    brewing,
    previewResult,
    fatalError,
    topNotices,
    arbiterNotices,
    dismissNotice,
    connected,
    promptChoice,
    clearPromptChoice,
    epilogueCards,
    interactionRequest,
    interactionProgress,
    handReveal,
    clearHandReveal,
    turnTimer,
    send,
  } = useGameSocket(nameSet ? code : "", name);

  const phase = gameState?.phase ?? "lobby";

  const me = gameState?.players.find((p) => p.id === myPlayerId);
  const myIndex =
    gameState?.players.findIndex((p) => p.id === myPlayerId) ?? -1;
  // Spectators observe but cannot act, whether assigned in the lobby or joined
  // after play began, so all play/pass/author controls stay hidden.
  // Spectators live in their own snapshot collection, not `players`.
  const isSpectator = Boolean(
    gameState?.spectators.some((s) => s.id === myPlayerId),
  );
  // The authoritative active player: players[turn_index]. Its roster index
  // also keys the identity color everywhere else at the table.
  const activeIndex =
    gameState && gameState.players.length
      ? gameState.turn_index % gameState.players.length
      : -1;
  const activePlayer =
    activeIndex >= 0 ? gameState?.players[activeIndex] : undefined;
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

  const hostId = gameState?.host_id ?? gameState?.players[0]?.id ?? null;
  const isHost = Boolean(gameState && myPlayerId && hostId === myPlayerId);
  const isGodHost = isHost && isSpectator;

  if (
    gameView !== "table" &&
    (phase !== "playing" || (gameView === "host" && !isHost))
  ) {
    setGameView("table");
  }

  const closeGameView = useCallback(() => {
    const closingView = gameView;
    setGameView("table");
    if (closingView === "table") return;
    window.requestAnimationFrame(() => {
      document
        .querySelector<HTMLButtonElement>(
          `[data-game-view-trigger="${closingView}"]`,
        )
        ?.focus({ preventScroll: true });
    });
  }, [gameView]);

  useEffect(() => {
    if (gameView === "table") return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeGameView();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeGameView, gameView]);

  useEffect(() => {
    if (phase === "playing" && gameView === "host" && isGodHost) {
      clearAdminGameState();
      send({ type: "admin_view", open: true });
      return () => {
        send({ type: "admin_view", open: false });
        clearAdminGameState();
      };
    }
    clearAdminGameState();
  }, [clearAdminGameState, gameView, isGodHost, phase, send]);

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

  // The exile zone ("removed from game"); its dock pile only appears once a
  // card has actually been exiled. `?? []` covers older servers without it.
  const exiled = gameState?.exiled ?? [];
  const topExiled: CardSnapshot | undefined = gameState
    ? gameState.cards[exiled[exiled.length - 1] ?? ""]
    : undefined;

  // Draw-pile size for the deck dock: the server redacts deck contents during
  // play, so the count field is the source of truth (deck.length covers older
  // servers that predate redaction).
  const deckCount = gameState
    ? (gameState.deck_count ?? gameState.deck.length)
    : 0;

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
  // My own hand's reveal status (reveal_hand op), rendered as a badge so the
  // owner knows who can see their cards.
  const myHandRevealedBadge = useMemo(() => {
    if (!gameState || !me) return null;
    if (me.hand_public) return "face up to everyone";
    const names = (me.hand_revealed_to ?? []).map(
      (id) => gameState.players.find((p) => p.id === id)?.name ?? id,
    );
    return names.length ? `revealed to ${names.join(", ")}` : null;
  }, [gameState, me]);

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
    <main className="flex h-dvh w-full max-w-full flex-col overflow-hidden">
      <InteractionPanel
        pending={gameState?.pending_interaction}
        request={interactionRequest}
        progressMessage={interactionProgress}
        cards={gameState?.cards ?? {}}
        roomCode={code}
        onSubmit={(interactionId, payload) =>
          send(interactionResponseMessage(interactionId, payload))
        }
      />
      <AdminProposalDialog
        proposal={gameState?.pending_admin_proposal}
        players={gameState?.players ?? []}
        spectators={gameState?.spectators ?? []}
        myPlayerId={myPlayerId}
        isHost={isHost}
        isSpectator={isSpectator}
        send={send}
      />
      {resultsAdminOpen && gameState?.phase === "results" && (
        <HostControlOverlay
          gameState={gameState}
          send={send}
          onClose={() => setResultsAdminOpen(false)}
        />
      )}
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
      <header
        data-game-header
        className="sticky top-0 z-40 flex flex-wrap items-center gap-x-2 gap-y-2 border-b-[2.5px] border-ink bg-card px-[max(0.75rem,env(safe-area-inset-left))] pt-[max(0.625rem,env(safe-area-inset-top))] pr-[max(0.75rem,env(safe-area-inset-right))] pb-2.5 shadow-[0_3px_0_rgba(26,26,26,0.08)] sm:gap-3.5 sm:px-5 sm:py-2.5 xl:flex-nowrap"
      >
        <Link
          href="/"
          className="shrink-0 font-marker text-xl leading-[0.95] !text-ink"
        >
          1KBWC
        </Link>
        <span className="h-6 w-0.5 bg-ink/20" />
        <span className="font-mono text-sm text-muted-foreground">{code}</span>
        <span className="hidden font-hand text-[17px] text-muted-foreground sm:inline">
          {PHASE_LABELS[phase]}
        </span>
        {phase === "playing" && gameState && (
          <>
            <CurrentTurnIndicator
              activeName={activePlayer?.name}
              isViewer={isActive}
              turnNumber={gameState.turn_number}
              color={activeIndex >= 0 ? playerColor(activeIndex) : undefined}
              className="max-w-[55vw] sm:max-w-none"
            />
            <TurnTimerChip timer={turnTimer} />
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
          <span className="sr-only sm:not-sr-only">
            {connected ? "connected" : "reconnecting…"}
          </span>
        </span>
        {phase === "playing" && gameState && (
          <GameNavTabs
            activeView={gameView}
            isHost={isHost}
            onViewChange={setGameView}
            className="order-last w-full xl:order-none xl:w-auto"
          />
        )}
      </header>

      <ViewportNoticeHost
        topNotices={topNotices}
        arbiterNotices={arbiterNotices}
        players={gameState?.players ?? []}
        onDismiss={dismissNotice}
        stackedGameNav={phase === "playing"}
      />

      <div
        data-game-workspace
        className="flex min-h-0 min-w-0 flex-1 overflow-hidden"
      >
        <div
          data-game-scroll
          className={cn(
            "min-w-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain",
            phase !== "playing" && "p-4",
          )}
        >
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
                <div className="flex w-full flex-col gap-4">
                  <LobbyRoster
                    title="Players"
                    people={gameState.players}
                    role="player"
                    hostId={hostId}
                    myPlayerId={myPlayerId}
                    canManage={isHost}
                    canMoveLastPlayer={gameState.players.length > 1}
                    send={send}
                  />
                  <LobbyRoster
                    title="Spectators"
                    people={gameState.spectators}
                    role="spectator"
                    hostId={hostId}
                    myPlayerId={myPlayerId}
                    canManage={isHost}
                    canMoveLastPlayer
                    send={send}
                  />
                </div>
                {isHost ? (
                  <Button
                    size="lg"
                    className="font-marker text-lg"
                    disabled={gameState.players.length === 0}
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
              <div className="flex min-h-full min-w-0 flex-col">
                <GameTable
                  gameState={gameState}
                  myPlayerId={myPlayerId ?? ""}
                />

                {/* felt table: center zone + deck/action dock */}
                <FeltDropZone className="mx-2 my-2.5 flex min-h-[320px] min-w-0 flex-1 flex-col items-stretch overflow-hidden rounded-[22px] border-[3px] border-ink bg-felt shadow-[inset_0_0_60px_rgba(0,0,0,0.18)] sm:mx-4 sm:min-h-[380px] sm:flex-row">
                  <HouseRulesZone
                    centerCards={houseRuleCards}
                    brewingCardId={brewing}
                    roomCode={code}
                  />
                  <div className="flex shrink-0 flex-row items-center justify-center gap-3.5 overflow-x-auto border-t-2 border-dashed border-white/30 bg-black/15 px-3 py-2 sm:flex-col sm:overflow-visible sm:border-t-0 sm:border-l-2 sm:px-5 sm:py-4">
                    <div className="text-center">
                      {deckCount > 0 ? (
                        <div className="relative mx-auto h-[104px] w-[74px] sm:h-32 sm:w-[92px]">
                          <SketchCard
                            faceDown
                            showTape={false}
                            w={compactViewport ? 74 : 92}
                            rot={3}
                            className="absolute top-1 left-1"
                          />
                          <SketchCard
                            faceDown
                            showTape={false}
                            w={compactViewport ? 74 : 92}
                            rot={-2}
                            className="absolute top-0.5 left-0.5"
                          />
                          <SketchCard
                            faceDown
                            showTape={false}
                            w={compactViewport ? 74 : 92}
                            className="absolute top-0 left-0"
                          />
                        </div>
                      ) : (
                        <div className="mx-auto flex h-[104px] w-[74px] items-center justify-center rounded-[7px] border-2 border-dashed border-white/40 font-hand text-sm text-white/70 sm:h-32 sm:w-[92px]">
                          empty
                        </div>
                      )}
                      <p className="mt-1.5 font-hand text-[15px] text-white">
                        Deck · {deckCount}
                      </p>
                    </div>
                    <DiscardPile
                      topCard={topDiscard}
                      count={gameState.discard.length}
                      roomCode={code}
                      onClick={() => setHistoryOpen(true)}
                    />
                    {exiled.length > 0 && (
                      <ExilePile
                        topCard={topExiled}
                        count={exiled.length}
                        roomCode={code}
                      />
                    )}
                  </div>
                </FeltDropZone>

                {/* your zone */}
                {isSpectator ? (
                  <div className="border-t-[2.5px] border-ink bg-card px-5 py-4">
                    <p className="mx-auto w-fit rounded-xl border-2 border-dashed border-ink/40 px-5 py-3 font-hand text-base text-muted-foreground">
                      You are spectating and cannot play cards.
                    </p>
                  </div>
                ) : (
                  <div
                    data-my-zone
                    data-active-turn={isActive || undefined}
                    className="min-w-0 border-t-[2.5px] border-ink bg-card px-3 pt-3 pb-4 sm:px-5"
                    style={
                      // Mirror of the active opponent seat's solid identity
                      // treatment: a steady color band along the zone's top.
                      isActive
                        ? {
                            boxShadow: `inset 0 4px 0 0 ${playerColor(myIndex)}`,
                          }
                        : undefined
                    }
                  >
                    <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2.5">
                      <div className="flex flex-wrap items-center gap-2.5">
                        {me && (
                          <>
                            <PlayerAvatar
                              name={me.name}
                              color={playerColor(myIndex)}
                              size={38}
                            />
                            <span className="font-hand text-[22px] leading-none">
                              {me.name}
                            </span>
                            {isActive && <CurrentTurnBadge label="Your turn" />}
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
                      revealedBadge={myHandRevealedBadge}
                    />
                  </div>
                )}

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
              onCorrectResults={() => setResultsAdminOpen(true)}
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
                canVote={Boolean(
                  myPlayerId &&
                  gameState.players.some((p) => p.id === myPlayerId),
                )}
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

        {gameState && phase === "playing" && gameView !== "table" && (
          <GameViewPanel
            view={gameView}
            gameState={
              gameView === "host" && isGodHost && adminGameState
                ? adminGameState
                : gameState
            }
            roomCode={code}
            log={log}
            brewing={brewing}
            presentation={wideGameView ? "sidebar" : "modal"}
            godMode={gameView === "host" && isGodHost}
            godModeLoading={
              gameView === "host" && isGodHost && adminGameState === null
            }
            send={send}
            onClose={closeGameView}
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
        cards={gameState?.cards ?? {}}
        roomCode={code}
        onPick={(choice) => {
          if (!promptChoice) return;
          // A prompt option carries either a player_id (player-target axis) or a
          // card_id (card-target axis). Merge the pick with the context the
          // prompt carried from earlier steps (a two-axis card prompts twice)
          // so the follow-up play re-sends the COMPLETE selection; the backend
          // re-interprets, validates, applies, and advances.
          // A reaction's prompt carries as_reaction; older servers omit it, so
          // fall back to the open-window heuristic (a prompt for any card other
          // than the suspended one is a reaction needing a target).
          const asReaction =
            promptChoice.as_reaction ??
            Boolean(
              pendingPlay && promptChoice.card_id !== pendingPlay.card_id,
            );
          const chosenPlayerId =
            choice.player_id ?? promptChoice.chosen_player_id;
          const chosenCardId = choice.card_id ?? promptChoice.chosen_card_id;
          send({
            type: "play",
            card_id: promptChoice.card_id,
            ...(chosenPlayerId ? { chosen_player_id: chosenPlayerId } : {}),
            ...(chosenCardId ? { chosen_card_id: chosenCardId } : {}),
            ...(asReaction ? { as_reaction: true } : {}),
          });
          clearPromptChoice();
        }}
        onCancel={clearPromptChoice}
      />

      <HandRevealDialog
        reveal={handReveal}
        roomCode={code}
        onDismiss={clearHandReveal}
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

function LobbyRoster({
  title,
  people,
  role,
  hostId,
  myPlayerId,
  canManage,
  canMoveLastPlayer,
  send,
}: {
  title: string;
  people: { id: string; name: string }[];
  role: "player" | "spectator";
  hostId: string | null;
  myPlayerId: string | null;
  canManage: boolean;
  canMoveLastPlayer: boolean;
  send: (message: ClientMsg) => void;
}) {
  return (
    <section>
      <h3 className="font-marker text-sm">{title}</h3>
      {people.length === 0 ? (
        <p className="font-hand text-sm italic text-muted-foreground">None.</p>
      ) : (
        <ul className="mt-1 flex flex-col gap-2">
          {people.map((person, index) => (
            <li
              key={person.id}
              className="flex flex-wrap items-center gap-2 rounded-xl border-2 border-ink/30 bg-panel-paper p-2"
            >
              <PlayerAvatar
                name={person.name}
                color={playerColor(index)}
                size={30}
              />
              <span className="min-w-0 flex-1 truncate font-hand text-lg">
                {person.name}
                {person.id === myPlayerId && " (you)"}
                {person.id === hostId && (
                  <span className="ml-1 text-sm text-muted-foreground">
                    · host
                  </span>
                )}
              </span>
              {canManage && person.id !== hostId && (
                <Button
                  variant="outline"
                  className="min-h-9 px-2 font-hand"
                  onClick={() =>
                    send({
                      type: "lobby_set_host",
                      participant_id: person.id,
                    })
                  }
                >
                  Make host
                </Button>
              )}
              {canManage && (
                <Button
                  variant="outline"
                  className="min-h-9 px-2 font-hand"
                  disabled={role === "player" && !canMoveLastPlayer}
                  onClick={() =>
                    send({
                      type: "lobby_set_role",
                      participant_id: person.id,
                      role: role === "player" ? "spectator" : "player",
                    })
                  }
                >
                  {role === "player" ? "Make spectator" : "Make player"}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
