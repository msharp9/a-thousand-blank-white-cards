import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CardChoiceGrid, cardChoiceLabel } from "./card-choice-grid";
import type { CardSnapshot } from "@/lib/types";

const faces: Record<string, CardSnapshot> = {
  c1: {
    id: "c1",
    title: "Pocket Volcano",
    description: "Everyone loses one point.",
    has_art: true,
  },
  c2: {
    id: "c2",
    title: "Tiny Umbrella",
    description: "Prevent the next point loss.",
  },
};

describe("CardChoiceGrid", () => {
  it("renders full card faces with art and reports the id on activation", async () => {
    const user = userEvent.setup();
    const onChoose = vi.fn();
    render(
      <CardChoiceGrid
        cardIds={["c1", "c2"]}
        faces={faces}
        roomCode="ROOM01"
        onChoose={onChoose}
      />,
    );
    expect(screen.getByText("Pocket Volcano")).toBeInTheDocument();
    expect(screen.getByText("Everyone loses one point.")).toBeInTheDocument();
    expect(
      screen.getByText("Prevent the next point loss."),
    ).toBeInTheDocument();
    expect(document.querySelector("img")?.getAttribute("src")).toContain(
      "/rooms/ROOM01/cards/c1/art",
    );
    const button = screen.getByRole("button", {
      name: "Choose Pocket Volcano",
    });
    expect(button).not.toHaveAttribute("aria-pressed");
    expect(button.className).toContain("focus-visible:ring");
    await user.click(button);
    expect(onChoose).toHaveBeenCalledWith("c1");
  });

  it("exposes aria-pressed selection state in multi mode", async () => {
    const user = userEvent.setup();
    const onChoose = vi.fn();
    render(
      <CardChoiceGrid
        cardIds={["c1", "c2"]}
        faces={faces}
        roomCode="ROOM01"
        selected={["c2"]}
        onChoose={onChoose}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Choose Pocket Volcano" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.getByRole("button", { name: "Choose Tiny Umbrella" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Selected ✓")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Choose Tiny Umbrella" }),
    );
    expect(onChoose).toHaveBeenCalledWith("c2");
  });

  it("falls back to a card-shaped placeholder that never leads with a raw id", () => {
    render(
      <CardChoiceGrid
        cardIds={["zz9"]}
        faces={{}}
        roomCode="ROOM01"
        names={{ zz9: "Mystery Card" }}
        onChoose={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Choose Mystery Card" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Mystery Card")).toBeInTheDocument();
    expect(screen.getByText("Card details unavailable")).toBeInTheDocument();
    expect(screen.queryByText("zz9")).toBeNull();
  });

  it("labels an unnamed missing face without exposing the id", () => {
    render(
      <CardChoiceGrid
        cardIds={["zz9"]}
        faces={{}}
        roomCode="ROOM01"
        onChoose={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Choose unknown card" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("zz9")).toBeNull();
  });
});

describe("cardChoiceLabel", () => {
  it("prefers the snapshot title, then the supplied name, never the id", () => {
    expect(cardChoiceLabel(faces.c1, "other")).toBe("Pocket Volcano");
    expect(cardChoiceLabel(undefined, "Named")).toBe("Named");
    expect(cardChoiceLabel(undefined)).toBe("unknown card");
  });
});
