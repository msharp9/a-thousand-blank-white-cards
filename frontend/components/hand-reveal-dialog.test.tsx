import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HandRevealDialog } from "./hand-reveal-dialog";
import type { HandRevealedMsg } from "@/lib/types";

const reveal: HandRevealedMsg = {
  type: "hand_revealed",
  player_id: "p1",
  player_name: "Alice",
  card_ids: ["a1", "a2"],
  cards: {
    a1: { id: "a1", title: "Zap", description: "Gain 5 points." },
    a2: { id: "a2", title: "Boom", description: "Lose 2 points." },
  },
};

describe("HandRevealDialog", () => {
  it("renders nothing without a reveal", () => {
    render(
      <HandRevealDialog reveal={null} roomCode="ROOM" onDismiss={vi.fn()} />,
    );
    expect(screen.queryByText(/reveals their hand/i)).toBeNull();
  });

  it("shows the revealed cards from the push payload", () => {
    render(
      <HandRevealDialog reveal={reveal} roomCode="ROOM" onDismiss={vi.fn()} />,
    );
    expect(screen.getByText(/Alice reveals their hand/i)).toBeTruthy();
    expect(screen.getByText("Zap")).toBeTruthy();
    expect(screen.getByText("Boom")).toBeTruthy();
  });

  it("dismisses via the dialog close affordance", async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    render(
      <HandRevealDialog
        reveal={reveal}
        roomCode="ROOM"
        onDismiss={onDismiss}
      />,
    );
    await user.click(screen.getByRole("button", { name: /close/i }));
    expect(onDismiss).toHaveBeenCalled();
  });

  it("opens the card inspector when a revealed card is clicked", async () => {
    const user = userEvent.setup();
    const onInspectCard = vi.fn();
    render(
      <HandRevealDialog
        reveal={reveal}
        roomCode="ROOM"
        onDismiss={vi.fn()}
        onInspectCard={onInspectCard}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Inspect Zap" }));
    expect(onInspectCard).toHaveBeenCalledWith(reveal.cards.a1);
  });

  it("handles an empty revealed hand", () => {
    render(
      <HandRevealDialog
        reveal={{ ...reveal, card_ids: [], cards: {} }}
        roomCode="ROOM"
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByText(/hand is empty/i)).toBeTruthy();
  });
});
