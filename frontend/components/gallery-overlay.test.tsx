import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { GalleryOverlay } from "./gallery-overlay";
import type {
  CardSnapshot,
  PlayerSnapshot,
  SpectatorSnapshot,
} from "@/lib/types";

function player(id: string, name: string): PlayerSnapshot {
  return {
    id,
    name,
    score: 0,
    hand: [],
    in_play: [],
    connected: true,
    conditions: {},
  };
}

function card(id: string, title: string, creator_id?: string): CardSnapshot {
  return { id, title, description: `Rule for ${title}`, creator_id };
}

describe("GalleryOverlay", () => {
  it("renders all cards sorted alphabetically by title with creator attribution", () => {
    const cards: Record<string, CardSnapshot> = {
      zap: card("zap", "Zap", "p1"),
      apple: card("apple", "Apple Toss", "p2"),
      unclaimed: card("unclaimed", "Mystery Card"),
    };
    const { container } = render(
      <GalleryOverlay
        cards={cards}
        players={[player("p1", "Alice"), player("p2", "Bob")]}
        spectators={[]}
        roomCode="ABCD"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("The Deck")).toBeTruthy();
    expect(screen.getByText("3 cards invented so far")).toBeTruthy();
    expect(screen.getByText("Apple Toss")).toBeTruthy();
    expect(screen.getByText("Mystery Card")).toBeTruthy();
    expect(screen.getByText("Zap")).toBeTruthy();

    // Alphabetical sort should land Apple Toss before Mystery Card before
    // Zap in render order, regardless of the fixture's own key order.
    const html = container.textContent ?? "";
    expect(html.indexOf("Apple Toss")).toBeLessThan(
      html.indexOf("Mystery Card"),
    );
    expect(html.indexOf("Mystery Card")).toBeLessThan(html.indexOf("Zap"));

    expect(screen.getByText("by Bob")).toBeTruthy();
    expect(screen.getByText("by Alice")).toBeTruthy();
    // No creator_id on the third card -> no attribution caption for it.
    expect(screen.getAllByText(/^by /)).toHaveLength(2);
  });

  it("resolves creator names from spectators too", () => {
    const cards: Record<string, CardSnapshot> = {
      a: card("a", "A Card", "s1"),
    };
    const spectators: SpectatorSnapshot[] = [
      { id: "s1", name: "Spectator Sam" },
    ];
    render(
      <GalleryOverlay
        cards={cards}
        players={[]}
        spectators={spectators}
        roomCode="ABCD"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("by Spectator Sam")).toBeTruthy();
  });

  it("shows an empty state with no cards", () => {
    render(
      <GalleryOverlay
        cards={{}}
        players={[]}
        spectators={[]}
        roomCode="ABCD"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("No cards invented yet.")).toBeTruthy();
    expect(screen.getByText("0 cards invented so far")).toBeTruthy();
  });

  it("caps initial render and reveals more on demand", async () => {
    const cards: Record<string, CardSnapshot> = {};
    for (let i = 0; i < 75; i++) {
      const id = `c${i}`;
      cards[id] = card(id, `Card ${String(i).padStart(3, "0")}`);
    }
    const user = userEvent.setup();
    render(
      <GalleryOverlay
        cards={cards}
        players={[]}
        spectators={[]}
        roomCode="ABCD"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("75 cards invented so far")).toBeTruthy();
    expect(screen.getByText("Card 000")).toBeTruthy();
    expect(screen.queryByText("Card 060")).toBeNull();
    const more = screen.getByRole("button", { name: /Show more/ });
    expect(more.textContent).toContain("15 left");

    await user.click(more);
    expect(screen.getByText("Card 060")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Show more/ })).toBeNull();
  });

  it("closes via the scrim and the close button, not via clicks inside the panel", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <GalleryOverlay
        cards={{ a: card("a", "A Card") }}
        players={[]}
        spectators={[]}
        roomCode="ABCD"
        onClose={onClose}
      />,
    );
    await user.click(screen.getByText("A Card"));
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Close gallery" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on a scrim tap", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <GalleryOverlay
        cards={{ a: card("a", "A Card") }}
        players={[]}
        spectators={[]}
        roomCode="ABCD"
        onClose={onClose}
      />,
    );
    await user.click(screen.getByTestId("gallery-scrim"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
