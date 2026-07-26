import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AdminProposalDialog } from "./admin-proposal-dialog";
import type { PendingAdminProposalSnapshot, PlayerSnapshot } from "@/lib/types";

const players: PlayerSnapshot[] = [
  {
    id: "host",
    name: "Alice",
    score: 0,
    hand: [],
    in_play: [],
    connected: true,
    conditions: {},
  },
  {
    id: "p2",
    name: "Bob",
    score: 0,
    hand: [],
    in_play: [],
    connected: false,
    conditions: {},
  },
];

function proposal(
  status: "waiting" | "approved" = "waiting",
): PendingAdminProposalSnapshot {
  return {
    proposal_id: "proposal-1",
    proposer_id: "host",
    phase: "playing",
    deadline_at: new Date(Date.now() + 60_000).toISOString(),
    preview: [
      {
        kind: "set_score",
        title: "Correct Bob’s score",
        detail: "Bob: 0 → 3 points",
      },
    ],
    warnings: ["Gameplay is paused until everyone decides."],
    voters: [{ player_id: "p2", status }],
  };
}

describe("AdminProposalDialog", () => {
  it("shows named vote status and sends a player's decision", async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    render(
      <AdminProposalDialog
        proposal={proposal()}
        players={players}
        myPlayerId="p2"
        isHost={false}
        isSpectator={false}
        send={send}
      />,
    );

    expect(screen.getByText("Alice · proposed")).toBeTruthy();
    expect(screen.getByText("Bob · waiting")).toBeTruthy();
    expect(screen.getByText("Bob: 0 → 3 points")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Accept change" }));
    expect(send).toHaveBeenCalledWith({
      type: "admin_vote",
      proposal_id: "proposal-1",
      accept: true,
    });
  });

  it("lets the host cancel while spectators remain read-only", async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    const { rerender } = render(
      <AdminProposalDialog
        proposal={proposal()}
        players={players}
        myPlayerId="host"
        isHost
        isSpectator={false}
        send={send}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Cancel proposal" }));
    expect(send).toHaveBeenCalledWith({
      type: "admin_cancel",
      proposal_id: "proposal-1",
    });

    rerender(
      <AdminProposalDialog
        proposal={proposal()}
        players={players}
        myPlayerId={null}
        isHost={false}
        isSpectator
        send={send}
      />,
    );
    expect(screen.getByText("You’re spectating this table vote.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Accept change" })).toBeNull();
  });
});
