"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { storePlayerId } from "@/lib/ws";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function LandingPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const nameValid = name.trim().length >= 1;

  function persistName() {
    localStorage.setItem("tbwc_player_name", name.trim());
  }

  async function handleCreate() {
    if (!nameValid) return;
    setLoading(true);
    setError(null);
    try {
      const createRes = await fetch(`${API_URL}/rooms`, { method: "POST" });
      if (!createRes.ok) throw new Error("Failed to create room");
      const { code } = await createRes.json();
      const joinRes = await fetch(`${API_URL}/rooms/${code}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!joinRes.ok) throw new Error("Failed to join room");
      const { player_id } = await joinRes.json();
      storePlayerId(player_id);
      persistName();
      router.push(`/room/${code}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function handleJoin() {
    if (!nameValid || joinCode.trim().length !== 6) return;
    setLoading(true);
    setError(null);
    try {
      const code = joinCode.trim().toUpperCase();
      const joinRes = await fetch(`${API_URL}/rooms/${code}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!joinRes.ok) throw new Error(joinRes.status === 404 ? "Room not found" : "Failed to join");
      const { player_id } = await joinRes.json();
      storePlayerId(player_id);
      persistName();
      router.push(`/room/${code}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-6 p-4">
      <div className="text-center">
        <h1 className="text-3xl font-bold">1000 Blank White Cards</h1>
        <p className="text-sm text-muted-foreground">The card game where you make the rules.</p>
      </div>

      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Play</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Input
            placeholder="Your name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={24}
          />
          <Button onClick={handleCreate} disabled={!nameValid || loading}>
            Create room
          </Button>
          <div className="flex gap-2">
            <Input
              placeholder="Room code"
              value={joinCode}
              onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
              maxLength={6}
            />
            <Button
              variant="outline"
              onClick={handleJoin}
              disabled={!nameValid || joinCode.trim().length !== 6 || loading}
            >
              Join
            </Button>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </CardContent>
      </Card>
    </main>
  );
}
