import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DiceRollOverlay } from "./dice-roll-overlay";
import type { DiceRollMsg } from "@/lib/types";

const roll: DiceRollMsg = {
  type: "dice_roll",
  actor_id: "p1",
  sides: 6,
  values: [3, 5],
  total: 8,
  card_id: "c1",
};

describe("DiceRollOverlay", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("tumbles first, then settles on the server values with the total", () => {
    render(<DiceRollOverlay roll={roll} actorName="Ana" />);
    expect(screen.getByText(/Ana rolls 2d6/)).toBeTruthy();
    // The total is withheld while the dice are still tumbling.
    expect(screen.queryByText("= 8")).toBeNull();

    act(() => {
      vi.advanceTimersByTime(800);
    });

    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("= 8")).toBeTruthy();
  });

  it("renders one die per value and no total for a single die", () => {
    render(
      <DiceRollOverlay
        roll={{ ...roll, values: [4], total: 4 }}
        actorName="Ana"
      />,
    );
    act(() => {
      vi.advanceTimersByTime(800);
    });
    expect(screen.getByText(/Ana rolls 1d6/)).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.queryByText(/^=/)).toBeNull();
  });
});
