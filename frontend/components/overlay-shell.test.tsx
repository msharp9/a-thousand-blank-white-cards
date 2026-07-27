import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OverlayShell } from "./overlay-shell";

function shell(presentation: "modal" | "sidebar", onClose = vi.fn()) {
  return (
    <OverlayShell
      scrimTestId="test-scrim"
      title="Test panel"
      closeLabel="Close test panel"
      presentation={presentation}
      onClose={onClose}
    >
      Panel body
    </OverlayShell>
  );
}

describe("OverlayShell", () => {
  it("uses modal semantics and closes through the backdrop", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(shell("modal", onClose));

    const dialog = screen.getByRole("dialog", { name: "Test panel" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(
      screen.getByRole("button", { name: "Close test panel" }),
    ).toHaveFocus();

    await user.click(screen.getByTestId("test-scrim"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("uses non-modal complementary semantics without a scrim", () => {
    render(shell("sidebar"));

    expect(
      screen.getByRole("complementary", { name: "Test panel" }),
    ).not.toHaveAttribute("aria-modal");
    expect(screen.queryByTestId("test-scrim")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Close test panel" }),
    ).not.toHaveFocus();
  });
});
