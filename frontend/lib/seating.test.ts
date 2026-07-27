import { describe, expect, it } from "vitest";
import {
  canonicalSeatOrder,
  projectSeats,
  SEAT_EDGE_LABELS,
  seatEdge,
} from "./seating";

const ROSTER = ["v", "a", "b", "c"];

describe("canonicalSeatOrder", () => {
  it("keeps a well-formed turn order as-is", () => {
    expect(canonicalSeatOrder(["b", "v", "c", "a"], ROSTER)).toEqual([
      "b",
      "v",
      "c",
      "a",
    ]);
  });

  it("drops ids not in the roster and duplicate ids", () => {
    expect(
      canonicalSeatOrder(["v", "ghost", "a", "v", "b", "c", "b"], ROSTER),
    ).toEqual(["v", "a", "b", "c"]);
  });

  it("appends roster ids missing from a malformed turn order, in roster order", () => {
    expect(canonicalSeatOrder(["c"], ROSTER)).toEqual(["c", "v", "a", "b"]);
    expect(canonicalSeatOrder([], ROSTER)).toEqual(ROSTER);
  });
});

describe("projectSeats", () => {
  it("puts the viewer's successor far-left and predecessor far-right", () => {
    expect(projectSeats(["v", "a", "b", "c"], ROSTER, "v")).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("rotates around a mid-order viewer without reversing", () => {
    expect(projectSeats(["a", "b", "v", "c"], ROSTER, "v")).toEqual([
      "c",
      "a",
      "b",
    ]);
  });

  it("reprojects immediately when the turn order reverses", () => {
    expect(projectSeats(["c", "b", "a", "v"], ROSTER, "v")).toEqual([
      "c",
      "b",
      "a",
    ]);
  });

  it("gives a spectator the full canonical order unchanged", () => {
    expect(projectSeats(["a", "b", "v", "c"], ROSTER, "spectator")).toEqual([
      "a",
      "b",
      "v",
      "c",
    ]);
  });

  it("normalizes malformed turn orders before projecting", () => {
    expect(projectSeats(["ghost", "b", "b"], ROSTER, "v")).toEqual([
      "a",
      "c",
      "b",
    ]);
  });

  it("handles a lone seated viewer and an empty roster", () => {
    expect(projectSeats(["v"], ["v"], "v")).toEqual([]);
    expect(projectSeats([], [], "v")).toEqual([]);
  });
});

describe("seatEdge", () => {
  it("marks the first opponent left and the last right", () => {
    expect(seatEdge(0, 3, true)).toBe("left");
    expect(seatEdge(1, 3, true)).toBeNull();
    expect(seatEdge(2, 3, true)).toBe("right");
  });

  it("marks a sole opponent as both neighbors", () => {
    expect(seatEdge(0, 1, true)).toBe("both");
  });

  it("gives spectators no edges", () => {
    expect(seatEdge(0, 3, false)).toBeNull();
    expect(seatEdge(2, 3, false)).toBeNull();
  });

  it("labels each edge for the table", () => {
    expect(SEAT_EDGE_LABELS.left).toBe("Left · next seat");
    expect(SEAT_EDGE_LABELS.right).toBe("Right · previous seat");
    expect(SEAT_EDGE_LABELS.both).toBe("Left & right neighbor");
  });
});
