import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReactionWindow } from "./reaction-window";
import type { CardSnapshot, ClientMsg, PendingPlaySnapshot } from "@/lib/types";

function pendingPlay(
  overrides: Partial<PendingPlaySnapshot> = {},
): PendingPlaySnapshot {
  return {
    window_id: "w1",
    card_id: "atk",
    actor_id: "p1",
    deadline_epoch_ms: Date.now() + 15_000,
    ...overrides,
  };
}

const zap: CardSnapshot = {
  id: "atk",
  title: "Zap",
  description: "Gain 5 points.",
};

const counterspell: CardSnapshot = {
  id: "cs",
  title: "Nuh-Uh",
  description: "Counter the pending card.",
  canonical: { trigger: "on_reaction" },
};

function windowUi({
  pending = pendingPlay(),
  myReactionCards = [counterspell],
  isActor = false,
  isSpectator = false,
  send = vi.fn<(msg: ClientMsg) => void>(),
} = {}) {
  render(
    <ReactionWindow
      pending={pending}
      pendingCard={zap}
      actorName="Alice"
      myReactionCards={myReactionCards}
      isActor={isActor}
      isSpectator={isSpectator}
      send={send}
      roomCode="ABCDEF"
    />,
  );
  return send;
}

afterEach(() => {
  vi.useRealTimers();
});

describe("ReactionWindow", () => {
  it("lets an eligible holder select a reaction and play it as_reaction", async () => {
    const user = userEvent.setup();
    const send = windowUi();
    // React is disabled until a card is selected.
    const reactButton = screen.getByRole("button", { name: /react/i });
    expect(reactButton).toBeDisabled();
    await user.click(screen.getByText("Nuh-Uh"));
    await user.click(reactButton);
    expect(send).toHaveBeenCalledWith({
      type: "play",
      card_id: "cs",
      as_reaction: true,
    });
  });

  it("sends pass_reaction with the window id and drops to the waiting banner", async () => {
    const user = userEvent.setup();
    const send = windowUi();
    await user.click(screen.getByRole("button", { name: /pass/i }));
    expect(send).toHaveBeenCalledWith({
      type: "pass_reaction",
      window_id: "w1",
    });
    // After passing, the modal is gone; the waiting banner shows instead.
    expect(screen.queryByRole("button", { name: /react/i })).toBeNull();
    expect(screen.getByText(/waiting to see if anyone reacts/i)).toBeTruthy();
  });

  it("shows the actor a waiting banner, never the react dialog", () => {
    windowUi({ isActor: true });
    expect(screen.queryByRole("button", { name: /react/i })).toBeNull();
    expect(screen.getByText(/waiting to see if anyone reacts/i)).toBeTruthy();
  });

  it("shows players without reaction cards the waiting banner", () => {
    windowUi({ myReactionCards: [] });
    expect(screen.queryByRole("button", { name: /react/i })).toBeNull();
    expect(screen.getByText(/waiting to see if anyone reacts/i)).toBeTruthy();
  });

  it("counts down against the server deadline", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2099-01-01T00:00:00.000Z"));
    windowUi({
      pending: pendingPlay({ deadline_epoch_ms: Date.now() + 10_000 }),
    });
    expect(screen.getByText("10s")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(4_000);
    });
    expect(screen.getByText("6s")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(20_000);
    });
    // Clamped at zero once the deadline passes (the server resolves the play).
    expect(screen.getByText("0s")).toBeTruthy();
  });

  it("re-prompts for a NEW window after passing an earlier one", async () => {
    const user = userEvent.setup();
    const send = vi.fn<(msg: ClientMsg) => void>();
    const { rerender } = render(
      <ReactionWindow
        pending={pendingPlay()}
        pendingCard={zap}
        actorName="Alice"
        myReactionCards={[counterspell]}
        isActor={false}
        isSpectator={false}
        send={send}
        roomCode="ABCDEF"
      />,
    );
    await user.click(screen.getByRole("button", { name: /pass/i }));
    expect(screen.queryByRole("button", { name: /react/i })).toBeNull();
    rerender(
      <ReactionWindow
        pending={pendingPlay({ window_id: "w2", card_id: "atk2" })}
        pendingCard={zap}
        actorName="Alice"
        myReactionCards={[counterspell]}
        isActor={false}
        isSpectator={false}
        send={send}
        roomCode="ABCDEF"
      />,
    );
    expect(screen.getByRole("button", { name: /react/i })).toBeTruthy();
  });
});
