import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { GameNavTabs, type GameView } from "./game-nav-tabs";

function NavHarness({ isHost = false }: { isHost?: boolean }) {
  const [activeView, setActiveView] = useState<GameView>("table");
  return (
    <GameNavTabs
      activeView={activeView}
      isHost={isHost}
      onViewChange={setActiveView}
    />
  );
}

describe("GameNavTabs", () => {
  it("defaults to Table and exposes History as the log view", () => {
    render(<NavHarness />);

    expect(screen.getByRole("button", { name: "Table" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "History" })).toHaveTextContent(
      "History",
    );
    expect(screen.getByRole("button", { name: "History" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("selects exactly one auxiliary view and returns to Table", async () => {
    const user = userEvent.setup();
    render(<NavHarness />);

    await user.click(screen.getByRole("button", { name: "Gallery" }));
    expect(screen.getByRole("button", { name: "Gallery" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Gallery" })).toHaveAttribute(
      "aria-controls",
      "game-view-panel",
    );

    await user.click(screen.getByRole("button", { name: "Scores" }));
    expect(screen.getByRole("button", { name: "Gallery" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByRole("button", { name: "Scores" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    await user.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.getByRole("button", { name: "Table" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("shows Host only to the host", () => {
    const { rerender } = render(<NavHarness />);
    expect(screen.queryByRole("button", { name: "Host" })).toBeNull();

    rerender(<NavHarness isHost />);
    for (const name of [
      "Table",
      "History",
      "Gallery",
      "Scores",
      "Rules",
      "Host",
    ]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
  });
});
