"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CreateCardDialog } from "@/components/create-card-dialog";
import { EffectLog } from "@/components/effect-log";
import { EpilogueView } from "@/components/epilogue";
import { GameTable } from "@/components/game-table";
import { Hand } from "@/components/hand";
import { HouseRulesZone } from "@/components/house-rules-zone";
import { SetupPhase } from "@/components/setup-phase";
import type { CardSnapshot } from "@/lib/types";
import { useGameSocket } from "@/lib/ws";

export default function RoomPage() {
  const params = useParams();
  const router = useRouter();
  const code = ((params.code as string) ?? "").toUpperCase();

  const [name, setName] = useState("");
  const [nameSet, setNameSet] = useState(false);
  const [myPlayerId, setMyPlayerId] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  // On mount, hydrate name + player id from localStorage.
  useEffect(() => {
    const storedName = localStorage.getItem("tbwc_player_name");
    if (storedName) {
      setName(storedName);
      setNameSet(true);
    }
    setMyPlayerId(localStorage.getItem("tbwc_player_id"));
  }, []);

  const { gameState, log, brewing, previewResult, error, connected, send } = useGameSocket(
    nameSet ? code : "",
    name,
  );

  // Keep myPlayerId fresh (the WS join may store it after connect).
  useEffect(() => {
    const id = localStorage.getItem("tbwc_player_id");
    if (id && id !== myPlayerId) setMyPlayerId(id);
  }, [connected, gameState, myPlayerId]);

  const phase = gameState?.phase ?? "lobby";

  // Resolve helpers.
  const me = gameState?.players.find((p) => p.id === myPlayerId);
  const isActive = useMemo(() => {
    if (!gameState || !gameState.players.length || !myPlayerId) return false;
    const active = gameState.players[gameState.turn_index % gameState.players.length];
    return active?.id === myPlayerId;
  }, [gameState, myPlayerId]);

  const myHandCards: CardSnapshot[] = useMemo(() => {
    if (!gameState || !me) return [];
    return me.hand.map((id) => gameState.cards[id]).filter((c): c is CardSnapshot => Boolean(c));
  }, [gameState, me]);

  const houseRuleCards: CardSnapshot[] = useMemo(() => {
    if (!gameState) return [];
    return gameState.house_rules.map((id) => gameState.cards[id]).filter((c): c is CardSnapshot => Boolean(c));
  }, [gameState]);

  const otherPlayers = useMemo(
    () => (gameState?.players ?? []).filter((p) => p.id !== myPlayerId).map((p) => ({ id: p.id, name: p.name })),
    [gameState, myPlayerId],
  );

  const isHost = Boolean(gameState && myPlayerId && gameState.players[0]?.id === myPlayerId);

  const epilogueCards: CardSnapshot[] = useMemo(
    () => (gameState ? Object.values(gameState.cards) : []),
    [gameState],
  );

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
          onKeyDown={(e) => e.key === "Enter" && name.trim() && setNameSet(true)}
        />
        <Button disabled={!name.trim()} onClick={() => setNameSet(true)}>
          Enter
        </Button>
      </main>
    );
  }

  if (!connected && !gameState) {
    return <main className="flex h-dvh items-center justify-center text-muted-foreground">Connecting to room {code}…</main>;
  }

  if (error) {
    return (
      <main className="flex h-dvh flex-col items-center justify-center gap-4 text-destructive">
        <p>{error}</p>
        <Button variant="outline" onClick={() => router.push("/")}>
          Back to lobby
        </Button>
      </main>
    );
  }

  return (
    <main className="flex h-dvh flex-col">
      <header className="flex items-center gap-3 border-b bg-background/80 px-4 py-2 backdrop-blur">
        <span className="font-mono text-sm text-muted-foreground">Room {code}</span>
        <span className="text-xs text-muted-foreground">{connected ? "Connected" : "Reconnecting…"}</span>
        <span className="ml-auto text-xs capitalize text-muted-foreground">{phase}</span>
      </header>

      <div className="flex-1 overflow-auto p-4">
        {!gameState && <p className="text-muted-foreground">Waiting for game state…</p>}

        {gameState && phase === "lobby" && (
          <div className="flex flex-col items-center gap-4">
            <p className="text-sm text-muted-foreground">Waiting in the lobby…</p>
            {isHost && <Button onClick={() => send({ type: "start" })}>Start game</Button>}
          </div>
        )}

        {gameState && phase === "setup" && (
          <SetupPhase gameState={gameState} myPlayerId={myPlayerId ?? ""} send={send} previewResult={previewResult} isHost={isHost} />
        )}

        {gameState && phase === "playing" && (
          <div className="flex flex-col gap-6">
            <GameTable gameState={gameState} myPlayerId={myPlayerId ?? ""} />
            <HouseRulesZone centerCards={houseRuleCards} brewingCardId={brewing} />
            <div className="flex items-center justify-between">
              <Button variant="outline" onClick={() => setDialogOpen(true)}>
                Author a card
              </Button>
              {isActive && <Button onClick={() => send({ type: "draw" })}>Draw</Button>}
            </div>
            <Hand cards={myHandCards} canPlay={isActive} otherPlayers={otherPlayers} send={send} />
            <EffectLog log={log} brewing={brewing} />
          </div>
        )}

        {gameState && phase === "epilogue" && <EpilogueView cards={epilogueCards} send={send} />}

        {gameState && phase === "ended" && (
          <div className="flex flex-col items-center gap-4">
            <h2 className="text-xl font-bold">Game over!</h2>
            <GameTable gameState={gameState} myPlayerId={myPlayerId ?? ""} />
            <Button variant="outline" onClick={() => router.push("/")}>
              Back to lobby
            </Button>
          </div>
        )}
      </div>

      <CreateCardDialog open={dialogOpen} onOpenChange={setDialogOpen} send={send} previewResult={previewResult} />
    </main>
  );
}
