import { describe, expect, it } from "vitest";
import {
  dismissNotice,
  enqueueNotice,
  parseArbiterLogEntry,
  type ViewportNotice,
} from "./notices";

const first: ViewportNotice = {
  id: "arbiter-1",
  lane: "arbiter",
  kind: "arbiter",
  message: "A bold interpretation.",
  timeoutMs: 7000,
};

describe("viewport notice queues", () => {
  it("recognizes only prefixed arbiter log entries", () => {
    expect(parseArbiterLogEntry("🤖  A bold interpretation. ")).toBe(
      "A bold interpretation.",
    );
    expect(parseArbiterLogEntry("Alice gains 2 points")).toBeNull();
    expect(parseArbiterLogEntry("🤖   ")).toBeNull();
  });

  it("keeps FIFO order and permits identical repeated messages", () => {
    const second = { ...first, id: "arbiter-2" };
    const queue = enqueueNotice(enqueueNotice([], first), second);
    expect(queue.map((notice) => notice.id)).toEqual([
      "arbiter-1",
      "arbiter-2",
    ]);
    expect(dismissNotice(queue, "arbiter-1")).toEqual([second]);
  });
});
