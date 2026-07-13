import { describe, expect, it } from "vitest";
import { interactionResponseMessage } from "./interactions";

describe("interactionResponseMessage", () => {
  it("builds the versioned client envelope", () => {
    expect(
      interactionResponseMessage("auction-1", { kind: "number", value: 7 }),
    ).toEqual({
      type: "interaction_response",
      schema_version: 1,
      interaction_id: "auction-1",
      payload: { kind: "number", value: 7 },
    });
  });
});
