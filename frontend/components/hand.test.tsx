import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Hand } from "./hand";
import type { CardSnapshot, ClientMsg } from "@/lib/types";

const zap: CardSnapshot = {
  id: "c1",
  title: "Zap",
  description: "Gain 5 points.",
};

const blank: CardSnapshot = {
  id: "b1",
  title: "",
  description: "",
  blank: true,
};

function handUi({
  cards = [zap, blank],
  canPlay = true,
  brewing = null as string | null,
  send = vi.fn<(msg: ClientMsg) => void>(),
} = {}) {
  const view = render(
    <Hand cards={cards} canPlay={canPlay} brewing={brewing} send={send} />,
  );
  return { send, ...view };
}

describe("Hand", () => {
  it("lets the active player select and play a card when nothing is brewing", async () => {
    const user = userEvent.setup();
    const { send } = handUi();
    await user.click(screen.getByText("Zap"));
    await user.click(screen.getByRole("button", { name: /^play$/i }));
    expect(send).toHaveBeenCalledWith({ type: "play", card_id: "c1" });
  });

  it("locks the hand while a play is brewing: no card is selectable, no Play action", () => {
    handUi({ brewing: "b1" });
    // Selectable cards render role="button"; while brewing none may (exactly
    // like the not-your-turn state), and the Play button is hidden too.
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    // The card under interpretation shows the brewing overlay.
    expect(screen.getByText(/interpreting/i)).toBeTruthy();
  });

  it("hides the Play action when brewing starts with a card already selected", async () => {
    const user = userEvent.setup();
    const send = vi.fn<(msg: ClientMsg) => void>();
    const { rerender } = render(
      <Hand cards={[zap, blank]} canPlay brewing={null} send={send} />,
    );
    await user.click(screen.getByText("Zap"));
    expect(screen.getByRole("button", { name: /^play$/i })).toBeTruthy();
    rerender(<Hand cards={[zap, blank]} canPlay brewing="b1" send={send} />);
    expect(screen.queryByRole("button", { name: /^play$/i })).toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("keeps the hand non-selectable off-turn regardless of brewing", () => {
    handUi({ canPlay: false });
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});
