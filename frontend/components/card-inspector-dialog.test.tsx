import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  CardInspectorDialog,
  CardInspectTrigger,
} from "./card-inspector-dialog";
import type { CardSnapshot } from "@/lib/types";

const card: CardSnapshot = {
  id: "c1",
  title: "Pocket Volcano",
  description: "Everyone loses one point.",
};

describe("CardInspectorDialog", () => {
  it("renders nothing without a card", () => {
    render(
      <CardInspectorDialog card={null} roomCode="ROOM" onDismiss={vi.fn()} />,
    );
    expect(screen.queryByText("Pocket Volcano")).toBeNull();
  });

  it("shows the card title and description as selectable text", () => {
    render(
      <CardInspectorDialog card={card} roomCode="ROOM" onDismiss={vi.fn()} />,
    );
    // The title/description appear twice: once as plain dialog text (the
    // selectable copy this dialog exists to add) and once baked into the
    // SketchCard face itself.
    expect(screen.getAllByText("Pocket Volcano").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("Everyone loses one point.").length,
    ).toBeGreaterThan(0);
  });

  it("dismisses via the dialog close affordance", async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    render(
      <CardInspectorDialog card={card} roomCode="ROOM" onDismiss={onDismiss} />,
    );
    await user.click(screen.getByRole("button", { name: /close/i }));
    expect(onDismiss).toHaveBeenCalled();
  });
});

describe("CardInspectTrigger", () => {
  it("reports the wrapped card on click", async () => {
    const user = userEvent.setup();
    const onInspect = vi.fn();
    render(
      <CardInspectTrigger card={card} onInspect={onInspect}>
        <span>mini</span>
      </CardInspectTrigger>,
    );
    await user.click(
      screen.getByRole("button", { name: "Inspect Pocket Volcano" }),
    );
    expect(onInspect).toHaveBeenCalledWith(card);
  });
});
