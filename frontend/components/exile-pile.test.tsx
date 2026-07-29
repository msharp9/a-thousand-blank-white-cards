import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExilePile } from "./exile-pile";
import type { CardSnapshot } from "@/lib/types";

const card: CardSnapshot = {
  id: "card-1",
  title: "Banished",
  description: "This card is out of the game.",
};

describe("ExilePile", () => {
  it("shows a ghost outline and the zero count when empty", () => {
    render(<ExilePile topCard={undefined} count={0} roomCode="ABCD" />);
    expect(screen.getByText("Removed · 0")).toBeTruthy();
    expect(screen.queryByText("Banished")).toBeNull();
  });

  it("renders the top exiled card and its count when non-empty", () => {
    render(<ExilePile topCard={card} count={2} roomCode="ABCD" />);
    expect(screen.getByText("Removed · 2")).toBeTruthy();
    expect(screen.getByText("Banished")).toBeTruthy();
  });
});
