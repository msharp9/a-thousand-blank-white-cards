import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ScoreboardOverlay } from "./scoreboard-overlay";
import { playerColor } from "@/lib/players";
import type { PlayerSnapshot } from "@/lib/types";

function player(
  overrides: Partial<PlayerSnapshot> & { id: string; name: string },
): PlayerSnapshot {
  return {
    score: 0,
    hand: [],
    in_play: [],
    connected: true,
    conditions: {},
    ...overrides,
  };
}

describe("ScoreboardOverlay", () => {
  it("sorts rows by score descending and shows hand/in-play counts", () => {
    const players: PlayerSnapshot[] = [
      player({ id: "p1", name: "Alice", score: 3, hand: ["a", "b"] }),
      player({ id: "p2", name: "Bob", score: 9, in_play: ["x"] }),
      player({ id: "p3", name: "Cara", score: 6 }),
    ];
    render(<ScoreboardOverlay players={players} onClose={() => {}} />);

    // Presence of each exact "<name> <medal>" string confirms both the sort
    // (highest score first) and the medal assignment in one assertion.
    expect(screen.getByText("Bob 🥇")).toBeTruthy();
    expect(screen.getByText("Cara 🥈")).toBeTruthy();
    expect(screen.getByText("Alice 🥉")).toBeTruthy();

    expect(screen.getByText("2 in hand · 0 in play")).toBeTruthy();
    expect(screen.getByText("0 in hand · 1 in play")).toBeTruthy();
  });

  it("colors each row by the player's turn-order identity, not standings order", () => {
    const players: PlayerSnapshot[] = [
      player({ id: "p1", name: "Alice", score: 1 }),
      player({ id: "p2", name: "Bob", score: 99 }),
    ];
    render(<ScoreboardOverlay players={players} onClose={() => {}} />);
    const bobScore = screen.getByText("99");
    const aliceScore = screen.getByText("1");
    expect(bobScore.style.color).toBe(playerColor(1));
    expect(aliceScore.style.color).toBe(playerColor(0));
  });

  it("closes via the close button", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <ScoreboardOverlay
        players={[player({ id: "p1", name: "Alice" })]}
        onClose={onClose}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Close scoreboard" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on a scrim tap", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <ScoreboardOverlay
        players={[player({ id: "p1", name: "Alice" })]}
        onClose={onClose}
      />,
    );
    await user.click(screen.getByTestId("scoreboard-scrim"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
