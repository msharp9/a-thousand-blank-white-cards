"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckIcon, Clock3Icon, ShieldCheckIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type {
  ClientMsg,
  PendingAdminProposalSnapshot,
  PlayerSnapshot,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface AdminProposalDialogProps {
  proposal: PendingAdminProposalSnapshot | null | undefined;
  players: PlayerSnapshot[];
  myPlayerId: string | null;
  isHost: boolean;
  isSpectator: boolean;
  send: (message: ClientMsg) => void;
}

export function AdminProposalDialog({
  proposal,
  players,
  myPlayerId,
  isHost,
  isSpectator,
  send,
}: AdminProposalDialogProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!proposal) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [proposal]);

  const playerNames = useMemo(
    () => new Map(players.map((player) => [player.id, player.name])),
    [players],
  );
  if (!proposal) return null;

  const me = proposal.voters.find((voter) => voter.player_id === myPlayerId);
  const secondsLeft = Math.max(
    0,
    Math.ceil((Date.parse(proposal.deadline_at) - now) / 1000),
  );
  const proposer = playerNames.get(proposal.proposer_id) ?? "The host";

  return (
    <div className="fixed inset-0 z-[80] flex items-stretch justify-center bg-[rgba(20,18,14,0.72)] sm:p-4">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-proposal-title"
        className="flex w-full max-w-2xl flex-col overflow-hidden border-[3px] border-ink bg-card pt-[env(safe-area-inset-top)] pr-[env(safe-area-inset-right)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] shadow-[8px_8px_0_rgba(26,26,26,0.8)] sm:rounded-[18px] sm:p-0"
      >
        <header className="shrink-0 border-b-2 border-ink px-4 py-3 sm:px-6">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="font-hand text-sm text-muted-foreground">
                {proposer} proposes a table correction
              </p>
              <h2
                id="admin-proposal-title"
                className="font-marker text-xl sm:text-2xl"
              >
                Everyone must agree
              </h2>
            </div>
            <span className="flex shrink-0 items-center gap-1 rounded-lg border-2 border-ink bg-panel-paper px-2 py-1 font-mono text-sm">
              <Clock3Icon className="size-4" />
              {secondsLeft}s
            </span>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 sm:px-6">
          <div className="flex flex-col gap-3">
            {proposal.preview.map((item, index) => (
              <article
                key={`${item.kind}-${index}`}
                className="-rotate-[0.2deg] rounded-xl border-2 border-ink bg-panel-paper p-3 panel-shadow"
              >
                <p className="font-marker text-sm text-primary">{item.title}</p>
                <p className="mt-1 font-hand text-lg leading-snug">
                  {item.detail}
                </p>
              </article>
            ))}
          </div>

          {proposal.warnings.length > 0 && (
            <div className="mt-4 rounded-xl border-2 border-amber bg-amber/10 p-3">
              <p className="font-marker text-sm">Heads up</p>
              <ul className="mt-1 list-disc pl-5 font-hand text-base">
                {proposal.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-5">
            <p className="font-marker text-sm">Table votes</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <span className="flex items-center gap-1 rounded-full border-2 border-marker-green bg-marker-green/10 px-3 py-1 font-hand">
                <ShieldCheckIcon className="size-4" />
                {proposer} · proposed
              </span>
              {proposal.voters.map((voter) => (
                <span
                  key={voter.player_id}
                  className={cn(
                    "flex items-center gap-1 rounded-full border-2 px-3 py-1 font-hand",
                    voter.status === "approved"
                      ? "border-marker-green bg-marker-green/10"
                      : "border-ink/40 bg-muted",
                  )}
                >
                  {voter.status === "approved" ? (
                    <CheckIcon className="size-4" />
                  ) : (
                    <Clock3Icon className="size-4" />
                  )}
                  {playerNames.get(voter.player_id) ?? voter.player_id} ·{" "}
                  {voter.status}
                </span>
              ))}
            </div>
          </div>
        </div>

        <footer className="shrink-0 border-t-2 border-ink bg-card px-4 py-3 sm:px-6">
          {isHost ? (
            <Button
              variant="outline"
              className="min-h-11 w-full border-destructive text-destructive"
              onClick={() =>
                send({
                  type: "admin_cancel",
                  proposal_id: proposal.proposal_id,
                })
              }
            >
              <XIcon />
              Cancel proposal
            </Button>
          ) : me?.status === "approved" ? (
            <p className="flex min-h-11 items-center justify-center gap-2 rounded-xl border-2 border-marker-green bg-marker-green/10 font-hand text-lg">
              <CheckIcon className="size-5" />
              Approved — waiting for the table…
            </p>
          ) : me ? (
            <div className="grid grid-cols-2 gap-3">
              <Button
                variant="outline"
                className="min-h-11 border-destructive text-destructive"
                onClick={() =>
                  send({
                    type: "admin_vote",
                    proposal_id: proposal.proposal_id,
                    accept: false,
                  })
                }
              >
                Reject
              </Button>
              <Button
                className="min-h-11 bg-marker-green text-white hover:bg-marker-green/90"
                onClick={() =>
                  send({
                    type: "admin_vote",
                    proposal_id: proposal.proposal_id,
                    accept: true,
                  })
                }
              >
                <CheckIcon />
                Accept change
              </Button>
            </div>
          ) : (
            <p className="min-h-11 text-center font-hand text-lg text-muted-foreground">
              {isSpectator
                ? "You’re spectating this table vote."
                : "Waiting for the required players…"}
            </p>
          )}
        </footer>
      </section>
    </div>
  );
}
