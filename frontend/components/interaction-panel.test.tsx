import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  InteractionPanel,
  drawingStrokesFromPayload,
  normalizeDrawingResponse,
} from "./interaction-panel";
import type {
  InteractionDescriptor,
  InteractionRequestMsg,
  InteractionResponsePayload,
  PendingInteractionSummary,
} from "@/lib/types";

const deadline = "2099-01-01T00:00:00.000Z";

function request(
  descriptor: Partial<InteractionDescriptor> &
    Pick<InteractionDescriptor, "kind">,
): InteractionRequestMsg {
  return {
    type: "interaction_request",
    schema_version: 1,
    interaction_id: "interaction-1",
    deadline_at: deadline,
    progress: {
      expected_count: 2,
      received_count: 0,
      submitted: false,
      complete: false,
    },
    descriptor: {
      schema_version: 1,
      prompt: "Answer the card",
      audience: "all",
      sealed: false,
      timeout_seconds: 60,
      ...descriptor,
    },
  };
}

const pending: PendingInteractionSummary = {
  interaction_id: "interaction-1",
  kind: "confirm",
  deadline_at: deadline,
  progress: {
    expected_count: 2,
    received_count: 0,
    submitted: false,
    complete: false,
  },
};

function panel(
  interactionRequest: InteractionRequestMsg | null,
  onSubmit = vi.fn<
    (interactionId: string, payload: InteractionResponsePayload) => void
  >(),
) {
  return {
    onSubmit,
    view: render(
      <InteractionPanel
        pending={pending}
        request={interactionRequest}
        progressMessage={null}
        cards={{ c1: { id: "c1", title: "The Card", description: "" } }}
        onSubmit={onSubmit}
      />,
    ),
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("InteractionPanel field renderers", () => {
  it("submits bounded choice selections", async () => {
    const user = userEvent.setup();
    const { onSubmit } = panel(
      request({
        kind: "choice",
        options: [
          { id: "a", label: "Cat A" },
          { id: "b", label: "Cat B" },
        ],
        min_selections: 1,
        max_selections: 1,
      }),
    );
    await user.click(screen.getByRole("button", { name: "Cat B" }));
    await user.click(screen.getByRole("button", { name: "Submit choice" }));
    expect(onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "choice",
      option_ids: ["b"],
    });
  });

  it("validates and submits a finite integer", async () => {
    const user = userEvent.setup();
    const { onSubmit } = panel(
      request({ kind: "number", minimum: 0, maximum: 10, integer: true }),
    );
    const input = screen.getByRole("spinbutton");
    const submit = screen.getByRole("button", { name: "Submit number" });
    await user.type(input, "2.5");
    expect(submit).toBeDisabled();
    await user.clear(input);
    await user.type(input, "7");
    await user.click(submit);
    expect(onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "number",
      value: 7,
    });
  });

  it("submits text, card picks, and confirmation values", async () => {
    const user = userEvent.setup();
    const text = panel(request({ kind: "text", max_length: 5 }));
    await user.type(screen.getByRole("textbox"), "abcdef");
    await user.click(screen.getByRole("button", { name: "Submit text" }));
    expect(text.onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "text",
      value: "abcde",
    });
    text.view.unmount();

    const pick = panel(request({ kind: "card_pick", card_ids: ["c1"] }));
    await user.click(screen.getByRole("button", { name: "The Card" }));
    expect(pick.onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "card_pick",
      card_id: "c1",
    });
    pick.view.unmount();

    const confirm = panel(
      request({
        kind: "confirm",
        confirm_label: "Absolutely",
        decline_label: "Nope",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Nope" }));
    expect(confirm.onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "confirm",
      confirmed: false,
    });
  });

  it("serializes normalized vector strokes from the drawing canvas", () => {
    const { onSubmit } = panel(
      request({ kind: "drawing", max_strokes: 4, max_points_per_stroke: 8 }),
    );
    const canvas = screen.getByRole("img", { name: "Drawing canvas" });
    Object.defineProperty(canvas, "getBoundingClientRect", {
      value: () => ({ left: 0, top: 0, width: 200, height: 100 }),
    });
    Object.defineProperty(canvas, "setPointerCapture", { value: vi.fn() });
    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 20, clientY: 20 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 180, clientY: 90 });
    fireEvent.pointerUp(canvas, { pointerId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Submit drawing" }));
    expect(onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "drawing",
      strokes: [
        {
          color: "#1a1a1a",
          width: 0.01,
          points: [
            { x: 0.1, y: 0.2 },
            { x: 0.9, y: 0.9 },
          ],
        },
      ],
    });
  });

  it("renders prior vector drawings as vote choices", () => {
    panel(
      request({
        kind: "choice",
        options: [
          {
            id: "p1",
            label: "Alice",
            payload: [
              { color: "#112233", width: 0.01, points: [{ x: 0, y: 1 }] },
            ],
          },
        ],
      }),
    );
    expect(
      screen.getByRole("img", { name: "Drawing submission" }),
    ).toBeInTheDocument();
  });
});

describe("InteractionPanel lifecycle", () => {
  it("shows counts without sealed values while waiting", () => {
    panel(null);
    expect(screen.getByText("0/2 submitted")).toBeInTheDocument();
    expect(screen.getByText(/Sealed answers stay hidden/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("recovers the form when a replayed request arrives", () => {
    const { view } = panel(null);
    expect(screen.getByText("Waiting for the table…")).toBeInTheDocument();
    view.rerender(
      <InteractionPanel
        pending={pending}
        request={request({ kind: "confirm" })}
        progressMessage={null}
        cards={{}}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Yes" })).toBeInTheDocument();
  });

  it("never submits a chained request under the prior stage id", () => {
    const next = request({ kind: "confirm" });
    next.interaction_id = "next-stage";
    const onSubmit = vi.fn();
    render(
      <InteractionPanel
        pending={pending}
        request={next}
        progressMessage={null}
        cards={{}}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByText("Waiting for the table…")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Yes" }),
    ).not.toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("disables input after the authoritative deadline", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2030-01-01T00:00:00Z"));
    const timed = request({ kind: "confirm" });
    timed.deadline_at = "2030-01-01T00:00:01Z";
    const timedPending = { ...pending, deadline_at: timed.deadline_at };
    render(
      <InteractionPanel
        pending={timedPending}
        request={timed}
        progressMessage={null}
        cards={{}}
        onSubmit={vi.fn()}
      />,
    );
    act(() => vi.advanceTimersByTime(1250));
    expect(screen.getByText("0s")).toBeInTheDocument();
    expect(
      screen.getByText("Time’s up — resolving the card…"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Yes" }),
    ).not.toBeInTheDocument();
  });

  it("never blanks or crashes on an unknown descriptor kind", () => {
    panel(request({ kind: "future_widget" }));
    expect(screen.getByRole("status")).toHaveTextContent("newer game client");
    expect(screen.getByText("Unknown kind: future_widget")).toBeInTheDocument();
  });
});

describe("drawing payload hardening", () => {
  it("drops hostile coordinates and clamps geometry", () => {
    expect(
      drawingStrokesFromPayload([
        {
          color: "javascript:red",
          width: Infinity,
          points: [
            { x: -1, y: 2 },
            { x: NaN, y: 0 },
          ],
        },
      ]),
    ).toEqual([
      {
        color: "#1a1a1a",
        width: 0.01,
        points: [{ x: 0, y: 1 }],
      },
    ]);
  });

  it("keeps wire drawings under the conservative post-parse budget", () => {
    const strokes = Array.from({ length: 64 }, () => ({
      color: "#123456",
      width: 0.01,
      points: Array.from({ length: 256 }, (_, index) => ({
        x: index / 255,
        y: 1 - index / 255,
      })),
    }));
    const normalized = normalizeDrawingResponse(strokes);
    expect(
      new TextEncoder().encode(
        JSON.stringify({ kind: "drawing", strokes: normalized }),
      ).byteLength,
    ).toBeLessThanOrEqual(48 * 1024);
  });
});
