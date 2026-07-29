import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { CardCreator } from "./card-creator";

function fake2dContext() {
  return {
    setTransform: vi.fn(),
    fillRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    fillText: vi.fn(),
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    lineJoin: "",
    lineCap: "",
    font: "",
    textAlign: "",
    textBaseline: "",
  };
}

let ctx: ReturnType<typeof fake2dContext>;

beforeAll(() => {
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: () => ctx,
  });
});

beforeEach(() => {
  ctx = fake2dContext();
});

function creator(capture?: {
  set?: ReturnType<typeof vi.fn>;
  release?: ReturnType<typeof vi.fn>;
  has?: (pointerId: number) => boolean;
}) {
  const view = render(
    <CardCreator
      title=""
      description=""
      onTitleChange={vi.fn()}
      onDescriptionChange={vi.fn()}
    />,
  );
  const canvas = screen.getByLabelText("Card drawing canvas");
  Object.defineProperty(canvas, "getBoundingClientRect", {
    value: () => ({ left: 0, top: 0, width: 360, height: 300 }),
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
  return { view, canvas };
}

describe("card creator drawing gesture hardening", () => {
  it("prevents default page gestures and captures the accepted pointer", () => {
    const set = vi.fn();
    const release = vi.fn();
    const { canvas } = creator({ set, release });
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

  it("applies gesture-blocking styles to the canvas", () => {
    const { canvas } = creator();
    expect(canvas.style.touchAction).toBe("none");
    expect(canvas.style.userSelect).toBe("none");
    expect(canvas.getAttribute("style")).toContain(
      "overscroll-behavior: contain",
    );
  });

  it("blocks native touch scrolling on the canvas but not outside it", () => {
    const { view, canvas } = creator();
    const touches = [{ clientX: 50, clientY: 50 }];
    expect(fireEvent.touchMove(canvas, { touches })).toBe(false);
    expect(
      fireEvent.touchMove(screen.getByLabelText("Card title"), { touches }),
    ).toBe(true);
    view.unmount();
    expect(fireEvent.touchMove(canvas, { touches })).toBe(true);
  });

  it("ignores secondary pointers so a second finger cannot corrupt the stroke", () => {
    const { canvas } = creator();
    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 20, clientY: 20 });
    expect(screen.queryByText(/draw something/)).toBeNull();
    expect(
      fireEvent.pointerDown(canvas, {
        pointerId: 2,
        clientX: 100,
        clientY: 50,
      }),
    ).toBe(true);
    expect(
      fireEvent.pointerMove(canvas, {
        pointerId: 2,
        clientX: 140,
        clientY: 70,
      }),
    ).toBe(true);
    fireEvent.pointerUp(canvas, { pointerId: 2 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 180, clientY: 90 });
    fireEvent.pointerUp(canvas, { pointerId: 1 });
    expect(ctx.lineTo).not.toHaveBeenCalledWith(140, 70);
    expect(ctx.lineTo).toHaveBeenCalledWith(180, 90);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByText(/draw something/)).toBeInTheDocument();
  });

  it("ends the stroke on pointer cancel and lost pointer capture", () => {
    const { canvas } = creator();
    fireEvent.pointerDown(canvas, { pointerId: 3, clientX: 20, clientY: 20 });
    fireEvent.pointerCancel(canvas, { pointerId: 3 });
    expect(
      fireEvent.pointerMove(canvas, {
        pointerId: 3,
        clientX: 180,
        clientY: 90,
      }),
    ).toBe(true);
    expect(ctx.lineTo).not.toHaveBeenCalledWith(180, 90);
    expect(
      fireEvent.pointerDown(canvas, {
        pointerId: 4,
        clientX: 100,
        clientY: 50,
      }),
    ).toBe(false);
    fireEvent.lostPointerCapture(canvas, { pointerId: 4 });
    expect(
      fireEvent.pointerMove(canvas, {
        pointerId: 4,
        clientX: 180,
        clientY: 90,
      }),
    ).toBe(true);
  });

  it("keeps undo, clear, and stamping working after hardened strokes", () => {
    const { canvas } = creator();
    for (const pointerId of [1, 2]) {
      fireEvent.pointerDown(canvas, { pointerId, clientX: 20, clientY: 20 });
      fireEvent.pointerUp(canvas, { pointerId });
    }
    expect(screen.queryByText(/draw something/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.queryByText(/draw something/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByText(/draw something/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stamp 🎉" }));
    fireEvent.pointerDown(canvas, { pointerId: 5, clientX: 100, clientY: 50 });
    expect(ctx.fillText).toHaveBeenCalledWith("🎉", 100, 50);
    expect(screen.queryByText(/draw something/)).toBeNull();
  });
});
