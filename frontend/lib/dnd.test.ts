import { describe, expect, it, vi } from "vitest";
import {
  FELT_DROP_ID,
  executeDrop,
  planDrop,
  playMessage,
  resolveDropPlay,
  seatDropId,
} from "./dnd";
import type { CardSnapshot, ClientMsg } from "@/lib/types";

const cards: Record<string, CardSnapshot> = {
  c1: { id: "c1", title: "Zap", description: "Gain 5 points." },
  b1: { id: "b1", title: "", description: "", blank: true },
};

function feltDrop(cardId: string) {
  return {
    active: { id: cardId },
    over: { id: FELT_DROP_ID, data: { current: { type: "felt" } } },
  };
}

function seatDrop(cardId: string, playerId: string) {
  return {
    active: { id: cardId },
    over: {
      id: seatDropId(playerId),
      data: { current: { type: "seat", playerId } },
    },
  };
}

describe("resolveDropPlay", () => {
  it("resolves a felt drop to an untargeted play", () => {
    expect(resolveDropPlay(feltDrop("c1"))).toEqual({
      cardId: "c1",
      targetPlayerId: null,
    });
  });

  it("resolves a seat drop to a play targeting that player", () => {
    expect(resolveDropPlay(seatDrop("c1", "p2"))).toEqual({
      cardId: "c1",
      targetPlayerId: "p2",
    });
  });

  it("returns null when the drag misses every drop target", () => {
    expect(resolveDropPlay({ active: { id: "c1" }, over: null })).toBeNull();
  });

  it("returns null for an unknown droppable", () => {
    expect(
      resolveDropPlay({
        active: { id: "c1" },
        over: { id: "mystery", data: { current: undefined } },
      }),
    ).toBeNull();
  });
});

describe("planDrop / executeDrop", () => {
  it("drop on felt sends the same play message as the Play button", () => {
    const send = vi.fn<(msg: ClientMsg) => void>();
    executeDrop(planDrop(feltDrop("c1"), cards), send, vi.fn());
    expect(send).toHaveBeenCalledWith({ type: "play", card_id: "c1" });
  });

  it("drop on a seat sends the play with chosen_player_id", () => {
    const send = vi.fn<(msg: ClientMsg) => void>();
    executeDrop(planDrop(seatDrop("c1", "p2"), cards), send, vi.fn());
    expect(send).toHaveBeenCalledWith({
      type: "play",
      card_id: "c1",
      chosen_player_id: "p2",
    });
  });

  it("drop of a BLANK opens the author-on-play dialog instead of sending", () => {
    const send = vi.fn<(msg: ClientMsg) => void>();
    const openAuthor = vi.fn();
    executeDrop(planDrop(seatDrop("b1", "p3"), cards), send, openAuthor);
    expect(send).not.toHaveBeenCalled();
    expect(openAuthor).toHaveBeenCalledWith({
      cardId: "b1",
      targetPlayerId: "p3",
    });
  });

  it("missed drop does nothing", () => {
    const send = vi.fn<(msg: ClientMsg) => void>();
    const openAuthor = vi.fn();
    executeDrop(
      planDrop({ active: { id: "c1" }, over: null }, cards),
      send,
      openAuthor,
    );
    expect(send).not.toHaveBeenCalled();
    expect(openAuthor).not.toHaveBeenCalled();
  });
});

describe("playMessage", () => {
  it("carries the authored blank fields and the seat target together", () => {
    expect(
      playMessage(
        { cardId: "b1", targetPlayerId: "p2" },
        { title: "Boom", description: "Lose 3 points.", art: "data:image/png" },
      ),
    ).toEqual({
      type: "play",
      card_id: "b1",
      chosen_player_id: "p2",
      title: "Boom",
      description: "Lose 3 points.",
      art: "data:image/png",
    });
  });

  it("omits target and art when absent", () => {
    expect(
      playMessage(
        { cardId: "b1", targetPlayerId: null },
        { title: "Boom", description: "Lose 3 points." },
      ),
    ).toEqual({
      type: "play",
      card_id: "b1",
      title: "Boom",
      description: "Lose 3 points.",
    });
  });
});
