import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CardSnapshot } from "@/lib/types";
import { EpilogueView } from "./epilogue";

const cards: CardSnapshot[] = [
  { id: "c1", title: "Chaos Goose", description: "Honks." },
  { id: "c2", title: "Point Fountain", description: "Points." },
];

describe("EpilogueView", () => {
  it("lets a seated player vote and signal done", async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    render(
      <EpilogueView cards={cards} send={send} isHost={false} canVote={true} />,
    );

    await user.click(screen.getAllByRole("button", { name: "Keep" })[0]);
    expect(send).toHaveBeenCalledWith({
      type: "epilogue_vote",
      card_id: "c1",
      keep: true,
    });

    await user.click(screen.getByRole("button", { name: "Skip remaining" }));
    expect(send).toHaveBeenCalledWith({ type: "epilogue_done" });
    // Non-host players never see Finalize.
    expect(screen.queryByRole("button", { name: "Finalize now" })).toBeNull();
  });

  it("shows a player host both voting controls and Finalize", () => {
    render(
      <EpilogueView
        cards={cards}
        send={vi.fn()}
        isHost={true}
        canVote={true}
      />,
    );
    expect(screen.getAllByRole("button", { name: "Keep" }).length).toBe(2);
    expect(screen.getByRole("button", { name: "Finalize now" })).toBeTruthy();
  });

  it("gives spectators a read-only view with no vote controls", () => {
    render(
      <EpilogueView
        cards={cards}
        send={vi.fn()}
        isHost={false}
        canVote={false}
      />,
    );
    expect(screen.getByText(/you're watching the vote/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Keep" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cut" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Done voting/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Skip remaining" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Finalize now" })).toBeNull();
    // The cards themselves remain visible to watch.
    expect(screen.getByText("Chaos Goose")).toBeTruthy();
  });

  it("keeps Finalize (and only Finalize) for a spectator host", async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    render(
      <EpilogueView cards={cards} send={send} isHost={true} canVote={false} />,
    );
    expect(screen.queryByRole("button", { name: "Keep" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cut" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Skip remaining" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Finalize now" }));
    expect(send).toHaveBeenCalledWith({ type: "epilogue_finalize" });
    expect(send).toHaveBeenCalledTimes(1);
  });
});
