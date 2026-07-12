"use client";

import { useCallback, useMemo, useState, useSyncExternalStore } from "react";
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
import { CreateCardDialog } from "@/components/create-card-dialog";
import { EffectLog } from "@/components/effect-log";
import { EpilogueView } from "@/components/epilogue";
import { GameTable } from "@/components/game-table";
import { Hand } from "@/components/hand";
import { HouseRulesZone } from "@/components/house-rules-zone";
import { SetupPhase } from "@/components/setup-phase";
import type {
  CardSnapshot,
  GameStateSnapshot,
  PromptChoiceMsg,
} from "@/lib/types";
import { getPlayerId, storePlayerId, useGameSocket } from "@/lib/ws";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
  const [dialogOpen, setDialogOpen] = useState(false);
  const [adoptedStoredName, setAdoptedStoredName] = useState(false);
  const [joining, setJoining] = useState(false);
  const [joinError, setJoinError] = useState<string | null>(null);

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
    send,
  } = useGameSocket(nameSet ? code : "", name);

  const phase = gameState?.phase ?? "lobby";

  // Resolve helpers.
  const me = gameState?.players.find((p) => p.id === myPlayerId);
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

  // ── name gate ──
  if (!nameSet) {
    return (
      <main className="flex h-dvh flex-col items-center justify-center gap-4 p-4">
        <p className="font-semibold">
          Enter your name to join room <span className="font-mono">{code}</span>
        </p>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Your name"
          maxLength={24}
          className="max-w-xs"
          onKeyDown={(e) => e.key === "Enter" && handleJoin()}
        />
        <Button disabled={!name.trim() || joining} onClick={handleJoin}>
          {joining ? "Joining…" : "Enter"}
        </Button>
        {joinError && <p className="text-sm text-destructive">{joinError}</p>}
      </main>
    );
  }

  // Surface a fatal join/WS rejection before the "Connecting" spinner so a hard
  // rejection (e.g. server closes with 4001) is shown instead of spinning
  // forever. Recoverable, message-level errors do NOT come through here — they
  // render as a transient banner over the live game (see below).
  if (fatalError) {
    return (
      <main className="flex h-dvh flex-col items-center justify-center gap-4 text-destructive">
        <p>{fatalError}</p>
        <Button variant="outline" onClick={() => router.push("/")}>
          Back to lobby
        </Button>
      </main>
    );
  }

  if (!connected && !gameState) {
    return (
      <main className="flex h-dvh items-center justify-center text-muted-foreground">
        Connecting to room {code}…
      </main>
    );
  }

  return (
    <main className="flex h-dvh flex-col">
      {/* Recoverable, message-level errors (e.g. "You have already drawn this
          turn") show as a dismissible banner over the live game — the table
          stays mounted and interactive. Auto-clears from the socket layer. */}
      {transientError && (
        <div
          role="alert"
          className="fixed inset-x-0 top-3 z-50 mx-auto flex w-fit max-w-[calc(100%-2rem)] items-center gap-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive shadow-sm backdrop-blur"
        >
          <span>{transientError}</span>
          <Button
            variant="ghost"
            size="icon-xs"
            className="text-destructive hover:bg-destructive/20"
            onClick={clearTransientError}
          >
            <XIcon />
            <span className="sr-only">Dismiss</span>
          </Button>
        </div>
      )}
      <header className="flex items-center gap-3 border-b bg-background/80 px-4 py-2 backdrop-blur">
        <span className="font-mono text-sm text-muted-foreground">
          Room {code}
        </span>
        <span className="text-xs text-muted-foreground">
          {connected ? "Connected" : "Reconnecting…"}
        </span>
        {isSpectator && (
          <span className="rounded bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
            Spectating
          </span>
        )}
        <span className="ml-auto text-xs capitalize text-muted-foreground">
          {phase}
        </span>
      </header>

      <div className="flex-1 overflow-auto p-4">
        {!gameState && (
          <p className="text-muted-foreground">Waiting for game state…</p>
        )}

        {gameState && phase === "lobby" && (
          <div className="flex flex-col items-center gap-4">
            <p className="text-sm text-muted-foreground">
              Waiting in the lobby…
            </p>
            {isHost && (
              <Button onClick={() => send({ type: "start" })}>
                Start game
              </Button>
            )}
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
          <div className="flex flex-col gap-6">
            <GameTable gameState={gameState} myPlayerId={myPlayerId ?? ""} />
            <HouseRulesZone
              centerCards={houseRuleCards}
              brewingCardId={brewing}
            />
            {isSpectator ? (
              <p className="text-sm italic text-muted-foreground">
                You joined after the game started — you are spectating and
                cannot play or author cards.
              </p>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <Button variant="outline" onClick={() => setDialogOpen(true)}>
                    Author a card
                  </Button>
                  {/* Turn begins with an explicit draw step: show Draw while the
                      active player hasn't drawn and the deck isn't empty. */}
                  {isActive &&
                    !gameState.has_drawn &&
                    gameState.deck.length > 0 && (
                      <Button onClick={() => send({ type: "draw" })}>
                        Draw a card
                      </Button>
                    )}
                  {/* End turn only when the player may pass (holds no playable
                      card) and has taken their draw step (or the deck is empty
                      so there's nothing to draw). */}
                  {isActive &&
                    gameState.can_pass &&
                    (gameState.has_drawn || gameState.deck.length === 0) && (
                      <Button
                        variant="secondary"
                        className="ml-auto"
                        onClick={() => send({ type: "pass" })}
                      >
                        End turn
                      </Button>
                    )}
                </div>
                {isActive && !gameState.has_drawn && (
                  <p className="text-sm font-medium text-primary">
                    Your turn — draw a card to begin.
                  </p>
                )}
                {/* Playing is gated until the draw step is taken. */}
                <Hand
                  cards={myHandCards}
                  canPlay={isActive && gameState.has_drawn}
                  send={send}
                />
              </>
            )}
            <EffectLog log={log} brewing={brewing} />
          </div>
        )}

        {gameState && phase === "epilogue" && (
          <div className="flex flex-col gap-4">
            {epilogueWinnerNames.length > 0 && (
              <div className="mx-auto rounded-lg border border-primary/40 bg-primary/10 px-4 py-2 text-center">
                <p className="text-sm font-semibold text-primary">
                  {epilogueWinnerNames.length > 1 ? "Winners" : "Winner"}:{" "}
                  {epilogueWinnerNames.join(", ")}
                </p>
              </div>
            )}
            <EpilogueView cards={epilogueCards} send={send} isHost={isHost} />
          </div>
        )}

        {gameState && phase === "ended" && (
          <EndScreen
            gameState={gameState}
            myPlayerId={myPlayerId ?? ""}
            onBack={() => router.push("/")}
          />
        )}
      </div>

      <CreateCardDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        send={send}
        previewResult={previewResult}
      />

      <TargetPickerDialog
        prompt={promptChoice}
        onPick={(choice) => {
          if (!promptChoice) return;
          // A prompt option carries either a player_id (player-target axis) or a
          // card_id (card-target axis). Re-send the play with the picked target;
          // the backend re-interprets, validates, applies, and advances.
          if (choice.player_id) {
            send({
              type: "play",
              card_id: promptChoice.card_id,
              chosen_player_id: choice.player_id,
            });
          } else if (choice.card_id) {
            send({
              type: "play",
              card_id: promptChoice.card_id,
              chosen_card_id: choice.card_id,
            });
          }
          clearPromptChoice();
        }}
        onCancel={clearPromptChoice}
      />
    </main>
  );
}

// Renders the target picker when the server asks the active player to choose a
// target for the card they just played. Picking sends a follow-up play carrying
// the choice; cancelling abandons the pending play (the turn never advanced).
function TargetPickerDialog({
  prompt,
  onPick,
  onCancel,
}: {
  prompt: PromptChoiceMsg | null;
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Choose a target</DialogTitle>
          {prompt && <DialogDescription>{prompt.prompt}</DialogDescription>}
        </DialogHeader>
        <div className="flex flex-col gap-2">
          {prompt?.choices.map((choice) => (
            <Button
              key={choice.player_id ?? choice.card_id}
              variant="outline"
              onClick={() => onPick(choice)}
            >
              {choice.name}
            </Button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

// Final win/lose screen. Winners come from the backend's authoritative
// winner_ids (mirrors GameState.winner_ids); we fall back to computing the
// highest score client-side only if the field is somehow absent, so an older
// snapshot still renders a sensible result.
function EndScreen({
  gameState,
  myPlayerId,
  onBack,
}: {
  gameState: GameStateSnapshot;
  myPlayerId: string;
  onBack: () => void;
}) {
  let winnerIds = gameState.winner_ids ?? [];
  if (winnerIds.length === 0 && gameState.players.length > 0) {
    const top = Math.max(...gameState.players.map((p) => p.score));
    winnerIds = gameState.players
      .filter((p) => p.score === top)
      .map((p) => p.id);
  }

  const iWon = winnerIds.includes(myPlayerId);
  const winnerNames = gameState.players
    .filter((p) => winnerIds.includes(p.id))
    .map((p) => p.name);

  let headline: string;
  if (winnerIds.length === 0) {
    headline = "Game over";
  } else if (iWon) {
    headline = winnerIds.length > 1 ? "You tied for the win!" : "You win! 🎉";
  } else {
    headline = "You lose";
  }

  return (
    <div className="flex flex-col items-center gap-4">
      <h2
        className={
          iWon
            ? "text-2xl font-bold text-primary"
            : "text-2xl font-bold text-muted-foreground"
        }
      >
        {headline}
      </h2>
      {winnerNames.length > 0 && (
        <p className="text-sm text-muted-foreground">
          {winnerNames.length > 1 ? "Winners" : "Winner"}:{" "}
          {winnerNames.join(", ")}
        </p>
      )}
      <GameTable gameState={gameState} myPlayerId={myPlayerId} />
      <Button variant="outline" onClick={onBack}>
        Back to lobby
      </Button>
    </div>
  );
}
