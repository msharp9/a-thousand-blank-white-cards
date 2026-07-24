import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TurnTimerChip } from "./turn-timer";

describe("TurnTimerChip", () => {
  it("renders nothing when no clock is armed", () => {
    const { container } = render(<TurnTimerChip timer={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("counts down from the server deadline", () => {
    render(
      <TurnTimerChip
        timer={{
          deadline_epoch_ms: Date.now() + 42_000,
          paused: false,
          player_id: "p1",
        }}
      />,
    );
    expect(screen.getByText(/⏱ 4[12]s/)).toBeInTheDocument();
  });

  it("shows a paused badge while the server has the clock suspended", () => {
    render(
      <TurnTimerChip
        timer={{ deadline_epoch_ms: null, paused: true, player_id: "p1" }}
      />,
    );
    expect(screen.getByText(/clock paused/)).toBeInTheDocument();
  });
});
