import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
});
