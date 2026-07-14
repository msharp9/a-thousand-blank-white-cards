import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { GalleryOverlay } from "./gallery-overlay";
import type {
  CardSnapshot,
  GameStateSnapshot,
  PlayerSnapshot,
  SpectatorSnapshot,
} from "@/lib/types";

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

function card(id: string, title: string, creator_id?: string): CardSnapshot {
  return { id, title, description: `Rule for ${title}`, creator_id };
}

function state(overrides: Partial<GameStateSnapshot> = {}): GameStateSnapshot {
  return {
    room_code: "ABCD",
    phase: "playing",
    players: [],
    spectators: [],
    turn_index: 0,
    turn_number: 1,
    turn_order: [],
    rules: {
      draw: 1,
      play: 1,
      cannot_play: {},
      end_condition: { type: "deck_empty" },
      win_condition: { kind: "highest_points" },
      extra: {},
    },
    draw_count: 1,
    deck: [],
    discard: [],
    cards: {},
    house_rules: [],
    hooks: [],
    has_drawn: true,
    can_pass: false,
    setup_progress: {},
    cards_to_author: 5,
    winner_ids: [],
    epilogue_result: null,
    history_events: [],
    log: [],
    ...overrides,
  };
}

describe("GalleryOverlay", () => {
  it("renders public cards sorted alphabetically by title with creator attribution", () => {
    const cards: Record<string, CardSnapshot> = {
      zap: card("zap", "Zap", "p1"),
      apple: card("apple", "Apple Toss", "p2"),
      unclaimed: card("unclaimed", "Mystery Card"),
    };
    const { container } = render(
      <GalleryOverlay
        gameState={state({
          cards,
          players: [
            player({ id: "p1", name: "Alice", in_play: ["zap"] }),
            player({ id: "p2", name: "Bob", in_play: ["apple"] }),
          ],
          discard: ["unclaimed"],
        })}
        roomCode="ABCD"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("The Deck")).toBeTruthy();
    expect(screen.getByText("3 cards played so far")).toBeTruthy();
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

  it("hides cards in hands and the undrawn deck, showing only public ones", () => {
    const cards: Record<string, CardSnapshot> = {
      played: card("played", "Played Out", "p1"),
      secret: card("secret", "Secret Hand Card", "p1"),
      buried: card("buried", "Buried In Deck"),
    };
    render(
      <GalleryOverlay
        gameState={state({
          cards,
          players: [
            player({
              id: "p1",
              name: "Alice",
              hand: ["secret"],
              in_play: ["played"],
            }),
          ],
          deck: ["buried"],
        })}
        roomCode="ABCD"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Played Out")).toBeTruthy();
    expect(screen.queryByText("Secret Hand Card")).toBeNull();
    expect(screen.queryByText("Buried In Deck")).toBeNull();
    expect(screen.getByText("1 card played so far")).toBeTruthy();
  });

  it("shows the deck-parked pre-made pool during setup", () => {
    const cards: Record<string, CardSnapshot> = {
      pool1: card("pool1", "Pool One"),
      pool2: card("pool2", "Pool Two"),
    };
    render(
      <GalleryOverlay
        gameState={state({
          phase: "setup",
          cards,
          deck: ["pool1", "pool2"],
        })}
        roomCode="ABCD"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Pool One")).toBeTruthy();
    expect(screen.getByText("Pool Two")).toBeTruthy();
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
        gameState={state({
          cards,
          players: [player({ id: "p1", name: "Alice", in_play: ["a"] })],
          spectators,
        })}
        roomCode="ABCD"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("by Spectator Sam")).toBeTruthy();
  });

  it("shows an empty state with no public cards", () => {
    render(
      <GalleryOverlay
        gameState={state({})}
        roomCode="ABCD"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("No cards played yet.")).toBeTruthy();
    expect(screen.getByText("0 cards played so far")).toBeTruthy();
  });

  it("caps initial render and reveals more on demand", async () => {
    const cards: Record<string, CardSnapshot> = {};
    const inPlay: string[] = [];
    for (let i = 0; i < 75; i++) {
      const id = `c${i}`;
      cards[id] = card(id, `Card ${String(i).padStart(3, "0")}`);
      inPlay.push(id);
    }
    const user = userEvent.setup();
    render(
      <GalleryOverlay
        gameState={state({
          cards,
          players: [player({ id: "p1", name: "Alice", in_play: inPlay })],
        })}
        roomCode="ABCD"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("75 cards played so far")).toBeTruthy();
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
        gameState={state({
          cards: { a: card("a", "A Card") },
          players: [player({ id: "p1", name: "Alice", in_play: ["a"] })],
        })}
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
        gameState={state({
          cards: { a: card("a", "A Card") },
          players: [player({ id: "p1", name: "Alice", in_play: ["a"] })],
        })}
        roomCode="ABCD"
        onClose={onClose}
      />,
    );
    await user.click(screen.getByTestId("gallery-scrim"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
