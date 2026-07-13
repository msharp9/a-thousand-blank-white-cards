import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DiscardPile } from "./discard-pile";
import type { CardSnapshot } from "@/lib/types";

const card: CardSnapshot = {
  id: "card-1",
  title: "Zap",
  description: "Skip the next player's turn.",
};

describe("DiscardPile", () => {
  it("shows a ghost outline and the zero count when empty", () => {
    render(<DiscardPile topCard={undefined} count={0} roomCode="ABCD" />);
    expect(screen.getByText("Discard · 0")).toBeTruthy();
    expect(screen.queryByText("Zap")).toBeNull();
  });

  it("renders the top card and its count when non-empty", () => {
    render(<DiscardPile topCard={card} count={3} roomCode="ABCD" />);
    expect(screen.getByText("Discard · 3")).toBeTruthy();
    expect(screen.getByText("Zap")).toBeTruthy();
  });

  it("is not a clickable role when no onClick is supplied", () => {
    render(<DiscardPile topCard={undefined} count={0} roomCode="ABCD" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("exposes a single clickable root when onClick is supplied", () => {
    render(
      <DiscardPile
        topCard={undefined}
        count={0}
        roomCode="ABCD"
        onClick={() => {}}
      />,
    );
    expect(screen.getByRole("button")).toBeTruthy();
  });

  it("calls onClick when the slot is clicked, including with a top card showing", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <DiscardPile
        topCard={card}
        count={3}
        roomCode="ABCD"
        onClick={onClick}
      />,
    );
    await user.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("calls onClick on Enter key for keyboard accessibility", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <DiscardPile
        topCard={undefined}
        count={0}
        roomCode="ABCD"
        onClick={onClick}
      />,
    );
    screen.getByRole("button").focus();
    await user.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
