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
        cards={{
          c1: { id: "c1", title: "The Card", description: "A shared rule." },
        }}
        roomCode="ROOM01"
        onSubmit={onSubmit}
      />,
    ),
  };
}

afterEach(() => {
  vi.useRealTimers();
});

function drawingCanvas(capture?: {
  set?: ReturnType<typeof vi.fn>;
  release?: ReturnType<typeof vi.fn>;
  has?: (pointerId: number) => boolean;
}) {
  const canvas = screen.getByRole("img", { name: "Drawing canvas" });
  Object.defineProperty(canvas, "getBoundingClientRect", {
    value: () => ({ left: 0, top: 0, width: 200, height: 100 }),
  });
  if (capture) {
    Object.defineProperty(canvas, "setPointerCapture", {
      value: capture.set ?? vi.fn(),
    });
    Object.defineProperty(canvas, "hasPointerCapture", {
      value: capture.has ?? (() => true),
    });
    Object.defineProperty(canvas, "releasePointerCapture", {
      value: capture.release ?? vi.fn(),
    });
  }
  return canvas;
}

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
    await user.click(screen.getByRole("button", { name: "Choose The Card" }));
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
    const canvas = drawingCanvas();
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

describe("drawing gesture hardening", () => {
  it("prevents default page gestures and captures the accepted pointer", () => {
    const set = vi.fn();
    const release = vi.fn();
    panel(request({ kind: "drawing" }));
    const canvas = drawingCanvas({ set, release });
    expect(
      fireEvent.pointerDown(canvas, { pointerId: 7, clientX: 20, clientY: 20 }),
    ).toBe(false);
    expect(set).toHaveBeenCalledWith(7);
    expect(
      fireEvent.pointerMove(canvas, { pointerId: 7, clientX: 60, clientY: 80 }),
    ).toBe(false);
    fireEvent.pointerUp(canvas, { pointerId: 7 });
    expect(release).toHaveBeenCalledWith(7);
    expect(
      fireEvent.pointerMove(canvas, { pointerId: 7, clientX: 90, clientY: 90 }),
    ).toBe(true);
  });

  it("applies gesture-blocking styles to the canvas and keeps the modal scrollable", () => {
    panel(request({ kind: "drawing" }));
    const canvas = drawingCanvas();
    expect(canvas.style.touchAction).toBe("none");
    expect(canvas.style.userSelect).toBe("none");
    expect(canvas.getAttribute("style")).toContain(
      "overscroll-behavior: contain",
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("overflow-y-auto");
    expect(dialog.className).toContain("overscroll-contain");
  });

  it("blocks native touch scrolling on the canvas but not the modal", () => {
    const { view } = panel(request({ kind: "drawing" }));
    const canvas = drawingCanvas();
    const touches = [{ clientX: 50, clientY: 50 }];
    expect(fireEvent.touchMove(canvas, { touches })).toBe(false);
    expect(fireEvent.touchMove(screen.getByRole("dialog"), { touches })).toBe(
      true,
    );
    view.rerender(
      <InteractionPanel
        pending={pending}
        request={request({ kind: "drawing" })}
        progressMessage={null}
        cards={{}}
        roomCode="ROOM01"
        onSubmit={vi.fn()}
      />,
    );
    expect(fireEvent.touchMove(canvas, { touches })).toBe(false);
    view.unmount();
    expect(fireEvent.touchMove(canvas, { touches })).toBe(true);
  });

  it("ignores secondary pointers so a second finger cannot corrupt the stroke", () => {
    const { onSubmit } = panel(request({ kind: "drawing" }));
    const canvas = drawingCanvas({});
    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 20, clientY: 20 });
    expect(
      fireEvent.pointerDown(canvas, {
        pointerId: 2,
        clientX: 100,
        clientY: 50,
      }),
    ).toBe(true);
    fireEvent.pointerMove(canvas, { pointerId: 2, clientX: 140, clientY: 70 });
    fireEvent.pointerUp(canvas, { pointerId: 2 });
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

  it("ends the stroke on pointer cancel and lost pointer capture", () => {
    const { onSubmit } = panel(request({ kind: "drawing" }));
    const canvas = drawingCanvas({});
    fireEvent.pointerDown(canvas, { pointerId: 3, clientX: 20, clientY: 20 });
    fireEvent.pointerCancel(canvas, { pointerId: 3 });
    fireEvent.pointerMove(canvas, { pointerId: 3, clientX: 180, clientY: 90 });
    fireEvent.pointerDown(canvas, { pointerId: 4, clientX: 100, clientY: 50 });
    fireEvent.lostPointerCapture(canvas, { pointerId: 4 });
    fireEvent.pointerMove(canvas, { pointerId: 4, clientX: 180, clientY: 90 });
    fireEvent.click(screen.getByRole("button", { name: "Submit drawing" }));
    expect(onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "drawing",
      strokes: [
        { color: "#1a1a1a", width: 0.01, points: [{ x: 0.1, y: 0.2 }] },
        { color: "#1a1a1a", width: 0.01, points: [{ x: 0.5, y: 0.5 }] },
      ],
    });
  });

  it("keeps Undo and Clear working after hardened strokes", () => {
    panel(request({ kind: "drawing" }));
    const canvas = drawingCanvas({});
    for (const pointerId of [1, 2]) {
      fireEvent.pointerDown(canvas, { pointerId, clientX: 20, clientY: 20 });
      fireEvent.pointerUp(canvas, { pointerId });
    }
    expect(screen.getByText(/2\/64 strokes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByText(/1\/64 strokes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByText(/0\/64 strokes/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Submit drawing" }),
    ).toBeDisabled();
  });
});

describe("card_pick full-card rendering", () => {
  it("renders full faces, preferring descriptor snapshots over shared state", () => {
    panel(
      request({
        kind: "card_pick",
        card_ids: ["c1", "h1"],
        cards: {
          h1: {
            id: "h1",
            title: "Hidden Card",
            description: "A rule from a hidden hand.",
          },
        },
      }),
    );
    expect(screen.getByText("A shared rule.")).toBeInTheDocument();
    expect(screen.getByText("Hidden Card")).toBeInTheDocument();
    expect(screen.getByText("A rule from a hidden hand.")).toBeInTheDocument();
  });

  it("toggles a multi pick with aria-pressed and submits the id set", async () => {
    const user = userEvent.setup();
    const { onSubmit } = panel(
      request({
        kind: "card_pick",
        card_ids: ["c1", "h1"],
        min_picks: 2,
        max_picks: 2,
        cards: {
          h1: { id: "h1", title: "Hidden Card", description: "Hush." },
        },
      }),
    );
    const first = screen.getByRole("button", { name: "Choose The Card" });
    const submit = screen.getByRole("button", { name: /Submit \d\/2/ });
    expect(first).toHaveAttribute("aria-pressed", "false");
    expect(submit).toBeDisabled();
    await user.click(first);
    expect(first).toHaveAttribute("aria-pressed", "true");
    await user.click(
      screen.getByRole("button", { name: "Choose Hidden Card" }),
    );
    await user.click(screen.getByRole("button", { name: "Submit 2/2" }));
    expect(onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "card_pick",
      card_ids: ["c1", "h1"],
    });
  });

  it("falls back to an accessible placeholder for a missing face", async () => {
    const user = userEvent.setup();
    const { onSubmit } = panel(
      request({ kind: "card_pick", card_ids: ["ghost-9"] }),
    );
    expect(screen.getByText("Card details unavailable")).toBeInTheDocument();
    expect(screen.queryByText("ghost-9")).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "Choose unknown card" }),
    );
    expect(onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "card_pick",
      card_id: "ghost-9",
    });
  });
});

describe("card_order (scry) renderer", () => {
  const scryRequest = () =>
    request({
      kind: "card_order",
      source: "deck_top",
      count: 3,
      card_ids: ["d1", "d2", "d3"],
      cards: {
        d1: { id: "d1", title: "First", description: "Rule one." },
        d2: { id: "d2", title: "Second", description: "Rule two." },
        d3: { id: "d3", title: "Third", description: "Rule three." },
      },
    });

  it("renders each offer as a full card face", () => {
    panel(scryRequest());
    for (const text of ["First", "Rule one.", "Second", "Rule two."]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
  });

  it("submits the untouched offer as the identity arrangement", async () => {
    const user = userEvent.setup();
    const { onSubmit } = panel(scryRequest());
    await user.click(screen.getByRole("button", { name: "Submit order" }));
    expect(onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "card_order",
      order: ["d1", "d2", "d3"],
      to_bottom: [],
    });
  });

  it("submits a reorder with a to-bottom split", async () => {
    const user = userEvent.setup();
    const { onSubmit } = panel(scryRequest());
    await user.click(screen.getByRole("button", { name: "Move Second up" }));
    await user.click(
      screen.getByRole("button", { name: "Send Third to the deck bottom" }),
    );
    await user.click(screen.getByRole("button", { name: "Submit order" }));
    expect(onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "card_order",
      order: ["d2", "d1"],
      to_bottom: ["d3"],
    });
  });

  it("returns a bottomed card to the top stack", async () => {
    const user = userEvent.setup();
    const { onSubmit } = panel(scryRequest());
    await user.click(
      screen.getByRole("button", { name: "Send First to the deck bottom" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Return First to the top" }),
    );
    await user.click(screen.getByRole("button", { name: "Submit order" }));
    expect(onSubmit).toHaveBeenCalledWith("interaction-1", {
      kind: "card_order",
      order: ["d2", "d3", "d1"],
      to_bottom: [],
    });
  });

  it("degrades safely when no cards are offered", () => {
    panel(request({ kind: "card_order", source: "deck_top", count: 3 }));
    expect(screen.getByRole("status")).toHaveTextContent(
      "No cards are available to reorder",
    );
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
        roomCode="ROOM01"
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
        roomCode="ROOM01"
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
        roomCode="ROOM01"
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
