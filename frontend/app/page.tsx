"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ButtonGroup } from "@/components/ui/button-group";
import { Input } from "@/components/ui/input";
import { SketchCard } from "@/components/sketch-card";
import { storePlayerId } from "@/lib/ws";
import type { Mode } from "@/lib/types";

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "both", label: "Both" },
  { value: "online", label: "Online" },
  { value: "in_person", label: "In-person" },
];

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const CORNER_DOODLES = [
  { emoji: "✏️", className: "top-[8%] left-[12%] text-[44px] -rotate-[14deg]" },
  { emoji: "🎲", className: "top-[16%] right-[10%] text-[40px] rotate-12" },
  {
    emoji: "🦆",
    className: "bottom-[12%] left-[16%] text-[38px] rotate-[8deg]",
  },
  {
    emoji: "🌋",
    className: "bottom-[16%] right-[14%] text-[42px] -rotate-[10deg]",
  },
];

const HOW_TO_STEPS = [
  "Draw a blank? Invent a card.",
  "Play cards, do what they say.",
  "Most points wins. Probably.",
];

export default function LandingPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [mode, setMode] = useState<Mode>("both");
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
      const createRes = await fetch(`${API_URL}/rooms`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      if (!createRes.ok) throw new Error("Failed to create room");
      const { code } = await createRes.json();
      const joinRes = await fetch(`${API_URL}/rooms/${code}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!joinRes.ok) throw new Error("Failed to join room");
      const { player_id } = await joinRes.json();
      storePlayerId(code, player_id);
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
      if (!joinRes.ok)
        throw new Error(
          joinRes.status === 404 ? "Room not found" : "Failed to join",
        );
      const { player_id } = await joinRes.json();
      storePlayerId(code, player_id);
      persistName();
      router.push(`/room/${code}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="relative flex min-h-dvh flex-col items-center justify-center overflow-hidden px-5 py-10">
      {CORNER_DOODLES.map((d) => (
        <span
          key={d.emoji}
          aria-hidden
          className={`pointer-events-none absolute opacity-50 ${d.className}`}
        >
          {d.emoji}
        </span>
      ))}

      <div className="mb-3 flex items-end">
        <div className="animate-floaty">
          <SketchCard
            card={{ id: "hero-blank", title: "", description: "", blank: true }}
            w={120}
            h={168}
            rot={-8}
          />
        </div>
        <div className="-ml-[26px] animate-floaty [animation-delay:0.6s]">
          <SketchCard
            title="Free Point! 🎉"
            description="You win. Somehow."
            w={120}
            h={168}
            rot={6}
          />
        </div>
        <div className="-ml-[26px] animate-floaty [animation-delay:1.2s]">
          <SketchCard
            title="Meow."
            description="Everyone must meow now."
            w={120}
            h={168}
            rot={-4}
          />
        </div>
      </div>

      <h1 className="mt-4 mb-1.5 text-center font-marker text-[clamp(38px,7vw,84px)] leading-[0.92] tracking-[-1px]">
        1000 Blank
        <br />
        White Cards
      </h1>
      <p className="mb-8 max-w-[520px] text-center font-hand text-[clamp(17px,2.4vw,24px)] text-muted-foreground">
        The card game where you make the rules.
      </p>

      <div className="w-full max-w-sm rounded-2xl border-[2.5px] border-ink bg-card p-5 panel-shadow -rotate-[0.5deg]">
        <div className="flex flex-col gap-4">
          <Input
            placeholder="Your name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={24}
            className="font-hand text-lg"
          />
          <div className="flex flex-col gap-1.5">
            <span className="font-hand text-base text-muted-foreground">
              Game mode
            </span>
            <ButtonGroup className="w-full">
              {MODE_OPTIONS.map((opt) => (
                <Button
                  key={opt.value}
                  type="button"
                  variant={mode === opt.value ? "default" : "outline"}
                  className="flex-1"
                  aria-pressed={mode === opt.value}
                  onClick={() => setMode(opt.value)}
                  disabled={loading}
                >
                  {opt.label}
                </Button>
              ))}
            </ButtonGroup>
          </div>
          <Button
            size="lg"
            className="font-marker text-lg"
            onClick={handleCreate}
            disabled={!nameValid || loading}
          >
            Create room
          </Button>
          <div className="flex gap-2">
            <Input
              placeholder="Room code"
              value={joinCode}
              onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
              maxLength={6}
              className="font-mono uppercase"
            />
            <Button
              variant="outline"
              onClick={handleJoin}
              disabled={!nameValid || joinCode.trim().length !== 6 || loading}
            >
              Join
            </Button>
          </div>
          {error && (
            <p className="font-hand text-base text-destructive">{error}</p>
          )}
        </div>
      </div>

      <div className="mt-10 flex flex-wrap justify-center gap-x-7 gap-y-2 font-hand text-lg text-muted-foreground">
        {HOW_TO_STEPS.map((step, i) => (
          <div key={step} className="flex items-center gap-2">
            <span className="text-[22px]">{i + 1}.</span> {step}
          </div>
        ))}
      </div>
    </main>
  );
}
