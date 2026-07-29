import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  CurrentTurnBadge,
  CurrentTurnIndicator,
} from "./current-turn-indicator";

describe("CurrentTurnIndicator", () => {
  it("announces the viewer's turn in a polite atomic status region", () => {
    render(<CurrentTurnIndicator activeName="Me" isViewer turnNumber={12} />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("YOUR TURN · Turn 12");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveAttribute("aria-atomic", "true");
  });

  it("names the active player when it is not the viewer's turn", () => {
    render(
      <CurrentTurnIndicator
        activeName="Alice"
        isViewer={false}
        turnNumber={3}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Alice's turn · Turn 3",
    );
  });

  it("updates the same live node across turn changes", () => {
    const { rerender } = render(
      <CurrentTurnIndicator
        activeName="Alice"
        isViewer={false}
        turnNumber={3}
      />,
    );
    const status = screen.getByRole("status");
    rerender(<CurrentTurnIndicator activeName="Me" isViewer turnNumber={4} />);
    expect(screen.getByRole("status")).toBe(status);
    expect(status).toHaveTextContent("YOUR TURN · Turn 4");
  });

  it("keeps the live region mounted but hidden without an active player", () => {
    render(
      <CurrentTurnIndicator
        activeName={undefined}
        isViewer={false}
        turnNumber={1}
      />,
    );
    const status = screen.getByRole("status", { hidden: true });
    expect(status).toHaveTextContent("");
    expect(status).toHaveClass("hidden");
  });

  it("shows the identity dot as decoration only", () => {
    render(
      <CurrentTurnIndicator
        activeName="Alice"
        isViewer={false}
        turnNumber={7}
        color="var(--player-1)"
      />,
    );
    const dot = document.querySelector("[aria-hidden]");
    expect(dot).not.toBeNull();
    expect((dot as HTMLElement).style.backgroundColor).toBe("var(--player-1)");
  });
});

describe("CurrentTurnBadge", () => {
  it("defaults to a Current turn label", () => {
    render(<CurrentTurnBadge />);
    expect(screen.getByText("Current turn")).toBeInTheDocument();
  });

  it("accepts the viewer-zone label", () => {
    render(<CurrentTurnBadge label="Your turn" />);
    expect(screen.getByText("Your turn")).toBeInTheDocument();
  });
});
