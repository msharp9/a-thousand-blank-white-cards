"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { Trash2, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverClose,
  PopoverContent,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { PLAYER_COLORS } from "@/lib/players";
import { useCompactAuthoringViewport } from "@/lib/use-compact-viewport";
import { cn } from "@/lib/utils";

// Backend WS frame budget: a data-URL longer than this is rejected server-side,
// so getArt() re-exports at smaller scales until the PNG fits.
const MAX_ART_CHARS = 131072;

const CANVAS_W = 360;
const CANVAS_H = 300;
const STAMP_FONT_PX = 48;
const EXPORT_SCALES = [2, 1.5, 1, 0.75, 0.5];

const INKS = [
  { name: "Ink", color: "#1a1a1a" },
  { name: "Red", color: PLAYER_COLORS[0] },
  { name: "Blue", color: PLAYER_COLORS[1] },
  { name: "Green", color: PLAYER_COLORS[2] },
  { name: "Yellow", color: "#F5D547" },
];

const NIBS = [
  { size: 2.5, dot: 2 },
  { size: 5, dot: 4 },
  { size: 9, dot: 8 },
];

const STAMPS = [
  "🎉",
  "🐱",
  "🦆",
  "🌋",
  "✏️",
  "🎲",
  "🔥",
  "⭐",
  "💀",
  "🍕",
  "👑",
  "🌈",
  "⚡",
  "🐙",
  "🧦",
];

type Stroke =
  | {
      type: "line";
      color: string;
      size: number;
      points: { x: number; y: number }[];
    }
  | { type: "stamp"; emoji: string; x: number; y: number };

function paintStrokes(
  ctx: CanvasRenderingContext2D,
  strokes: Stroke[],
  scale: number,
) {
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  for (const stroke of strokes) {
    if (stroke.type === "stamp") {
      ctx.font = `${STAMP_FONT_PX}px serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(stroke.emoji, stroke.x, stroke.y);
    } else {
      ctx.strokeStyle = stroke.color;
      ctx.lineWidth = stroke.size;
      ctx.beginPath();
      stroke.points.forEach((p, i) =>
        i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y),
      );
      // A tap leaves a single point; nudge so the round cap paints a dot.
      if (stroke.points.length === 1) {
        ctx.lineTo(stroke.points[0].x + 0.1, stroke.points[0].y + 0.1);
      }
      ctx.stroke();
    }
  }
}

export interface CardCreatorHandle {
  /**
   * PNG data-URL of the drawing, or null when nothing was drawn. Guaranteed to
   * fit the backend's frame budget: re-exported at progressively smaller
   * scales (strokes are vector data, so they redraw cleanly) until it fits.
   */
  getArt: () => string | null;
  /** Wipe the canvas and toolbar arming (title/description are the caller's). */
  reset: () => void;
}

interface CardCreatorProps {
  title: string;
  description: string;
  onTitleChange: (value: string) => void;
  onDescriptionChange: (value: string) => void;
  /** Short line under the card explaining where it goes (flow-specific). */
  caption?: string;
}

/**
 * The card-creator studio (design §3): an editable card face — title input,
 * freehand drawing canvas, rule textarea — with ink/nib/undo/clear tools and
 * an emoji stamp grid. Pure authoring surface: no WS knowledge; the owning
 * dialog reads the drawing via the imperative handle and submits.
 */
export const CardCreator = forwardRef<CardCreatorHandle, CardCreatorProps>(
  function CardCreator(
    { title, description, onTitleChange, onDescriptionChange, caption },
    ref,
  ) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const strokesRef = useRef<Stroke[]>([]);
    const currentStrokeRef = useRef<Stroke | null>(null);
    const [strokeCount, setStrokeCount] = useState(0);
    const [ink, setInk] = useState(INKS[0].color);
    const [nib, setNib] = useState(NIBS[1].size);
    const [armedStamp, setArmedStamp] = useState<string | null>(null);
    const compact = useCompactAuthoringViewport();

    const redraw = useCallback(() => {
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext("2d");
      if (!canvas || !ctx) return;
      paintStrokes(ctx, strokesRef.current, canvas.width / CANVAS_W);
    }, []);

    // Size the backing store for the device pixel ratio so exports and the
    // on-screen drawing stay crisp; strokes live in CANVAS_W×CANVAS_H space.
    useEffect(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const dpr = Math.max(1, window.devicePixelRatio || 1);
      canvas.width = Math.round(CANVAS_W * dpr);
      canvas.height = Math.round(CANVAS_H * dpr);
      redraw();
    }, [redraw]);

    useImperativeHandle(
      ref,
      () => ({
        getArt() {
          if (strokesRef.current.length === 0) return null;
          const doc = canvasRef.current?.ownerDocument ?? document;
          for (const scale of EXPORT_SCALES) {
            const off = doc.createElement("canvas");
            off.width = Math.round(CANVAS_W * scale);
            off.height = Math.round(CANVAS_H * scale);
            const ctx = off.getContext("2d");
            if (!ctx) return null;
            paintStrokes(ctx, strokesRef.current, scale);
            const url = off.toDataURL("image/png");
            if (url.length <= MAX_ART_CHARS) return url;
          }
          return null;
        },
        reset() {
          strokesRef.current = [];
          currentStrokeRef.current = null;
          setStrokeCount(0);
          setArmedStamp(null);
          redraw();
        },
      }),
      [redraw],
    );

    function canvasPos(e: React.PointerEvent<HTMLCanvasElement>) {
      const rect = e.currentTarget.getBoundingClientRect();
      return {
        x: (e.clientX - rect.left) * (CANVAS_W / rect.width),
        y: (e.clientY - rect.top) * (CANVAS_H / rect.height),
      };
    }

    function handlePointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
      const active = e.currentTarget.ownerDocument.activeElement;
      if (
        active instanceof HTMLInputElement ||
        active instanceof HTMLTextAreaElement
      ) {
        active.blur();
      }
      e.currentTarget.focus({ preventScroll: true });
      e.preventDefault();
      const p = canvasPos(e);
      if (armedStamp) {
        strokesRef.current.push({ type: "stamp", emoji: armedStamp, ...p });
        setArmedStamp(null);
        setStrokeCount((n) => n + 1);
        redraw();
        return;
      }
      e.currentTarget.setPointerCapture(e.pointerId);
      const stroke: Stroke = {
        type: "line",
        color: ink,
        size: nib,
        points: [p],
      };
      currentStrokeRef.current = stroke;
      strokesRef.current.push(stroke);
      setStrokeCount((n) => n + 1);
      redraw();
    }

    function handlePointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
      const stroke = currentStrokeRef.current;
      if (!stroke || stroke.type !== "line") return;
      e.preventDefault();
      stroke.points.push(canvasPos(e));
      redraw();
    }

    function endStroke() {
      currentStrokeRef.current = null;
    }

    function undo() {
      strokesRef.current.pop();
      currentStrokeRef.current = null;
      setStrokeCount((n) => Math.max(0, n - 1));
      redraw();
    }

    function clear() {
      strokesRef.current = [];
      currentStrokeRef.current = null;
      setStrokeCount(0);
      redraw();
    }

    const canvasEmpty = strokeCount === 0;

    return (
      <div
        data-card-creator
        data-layout={compact ? "compact" : "expanded"}
        className={cn(
          "grid min-w-0 items-start gap-3",
          compact
            ? "h-full w-full grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)] gap-2"
            : "grid-cols-[auto_minmax(0,400px)_190px] justify-center gap-4",
        )}
      >
        {compact ? (
          <div
            data-card-tools
            className="flex min-w-0 items-center justify-center gap-1"
          >
            <Popover>
              <PopoverTrigger
                render={
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="size-11"
                    title="Choose ink color"
                  />
                }
              >
                <span
                  className="size-5 rounded-full border-2 border-ink"
                  style={{ background: ink }}
                />
                <span className="sr-only">
                  Ink color: {INKS.find((pen) => pen.color === ink)?.name}
                </span>
              </PopoverTrigger>
              <PopoverContent data-card-ink-palette>
                <PopoverTitle className="mb-2">Ink color</PopoverTitle>
                <div className="grid grid-cols-5 gap-1.5">
                  {INKS.map((pen) => (
                    <PopoverClose
                      key={pen.color}
                      type="button"
                      aria-label={pen.name}
                      aria-pressed={ink === pen.color && !armedStamp}
                      onClick={() => {
                        setInk(pen.color);
                        setArmedStamp(null);
                      }}
                      className={cn(
                        "size-11 cursor-pointer rounded-full border-2 border-ink outline-none focus-visible:ring-3 focus-visible:ring-ring",
                        ink === pen.color &&
                          !armedStamp &&
                          "ring-[3px] ring-ring ring-offset-1",
                      )}
                      style={{ background: pen.color }}
                    />
                  ))}
                </div>
              </PopoverContent>
            </Popover>

            <Popover>
              <PopoverTrigger
                render={
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="size-11"
                    title="Choose nib size"
                  />
                }
              >
                <span
                  className="block w-5 rounded-full bg-ink"
                  style={{ height: Math.min(nib, 8) }}
                />
                <span className="sr-only">Nib size: {nib}px</span>
              </PopoverTrigger>
              <PopoverContent data-card-nib-palette>
                <PopoverTitle className="mb-2">Nib size</PopoverTitle>
                <div className="grid grid-cols-3 gap-1.5">
                  {NIBS.map((option) => (
                    <PopoverClose
                      key={option.size}
                      type="button"
                      aria-label={`${option.size}px nib`}
                      aria-pressed={nib === option.size}
                      onClick={() => {
                        setNib(option.size);
                        setArmedStamp(null);
                      }}
                      className={cn(
                        "flex size-11 cursor-pointer items-center justify-center rounded-lg border-2 border-ink bg-card outline-none focus-visible:ring-3 focus-visible:ring-ring",
                        nib === option.size &&
                          "ring-[3px] ring-ring ring-offset-1",
                      )}
                    >
                      <span
                        className="block w-5 rounded-full bg-ink"
                        style={{ height: option.dot }}
                      />
                    </PopoverClose>
                  ))}
                </div>
              </PopoverContent>
            </Popover>

            <Popover>
              <PopoverTrigger
                render={
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="size-11 text-xl"
                    title="Choose a stamp"
                  />
                }
              >
                <span aria-hidden>{armedStamp ?? "★"}</span>
                <span className="sr-only">
                  {armedStamp ? `Stamp armed: ${armedStamp}` : "Choose a stamp"}
                </span>
              </PopoverTrigger>
              <PopoverContent data-card-stamp-palette>
                <PopoverTitle className="mb-2">Choose a stamp</PopoverTitle>
                <div className="grid grid-cols-5 gap-1.5">
                  {STAMPS.map((emoji) => (
                    <PopoverClose
                      key={emoji}
                      type="button"
                      aria-label={`Stamp ${emoji}`}
                      aria-pressed={armedStamp === emoji}
                      onClick={() => setArmedStamp(emoji)}
                      className={cn(
                        "flex size-11 cursor-pointer items-center justify-center rounded-lg border-[1.5px] border-ink bg-card text-lg outline-none focus-visible:ring-3 focus-visible:ring-ring",
                        armedStamp === emoji &&
                          "border-2 border-ring bg-accent",
                      )}
                    >
                      {emoji}
                    </PopoverClose>
                  ))}
                </div>
              </PopoverContent>
            </Popover>

            <Button
              type="button"
              variant="outline"
              size="icon"
              className="size-11"
              title="Undo"
              aria-label="Undo"
              onClick={undo}
            >
              <Undo2 />
            </Button>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="size-11"
              title="Clear"
              aria-label="Clear"
              onClick={clear}
            >
              <Trash2 />
            </Button>
          </div>
        ) : (
          <div
            data-card-tools
            className="flex flex-col items-stretch gap-2 rounded-2xl border-[2.5px] border-ink bg-card p-2.5 panel-shadow"
          >
            <div className="font-marker text-center text-xs">Ink</div>
            {INKS.map((pen) => (
              <button
                key={pen.color}
                type="button"
                title={pen.name}
                aria-label={pen.name}
                aria-pressed={ink === pen.color && !armedStamp}
                onClick={() => {
                  setInk(pen.color);
                  setArmedStamp(null);
                }}
                className={cn(
                  "size-8 cursor-pointer rounded-full border-2 border-ink",
                  ink === pen.color &&
                    !armedStamp &&
                    "ring-[3px] ring-ring ring-offset-1",
                )}
                style={{ background: pen.color }}
              />
            ))}
            <div className="my-0.5 h-0.5 w-auto bg-muted" />
            <div className="font-marker text-center text-xs">Nib</div>
            {NIBS.map((option) => (
              <button
                key={option.size}
                type="button"
                title={`${option.size}px`}
                aria-label={`${option.size}px nib`}
                aria-pressed={nib === option.size}
                onClick={() => {
                  setNib(option.size);
                  setArmedStamp(null);
                }}
                className={cn(
                  "flex size-8 cursor-pointer items-center justify-center rounded-lg border-2 border-ink bg-card",
                  nib === option.size && "ring-[3px] ring-ring ring-offset-1",
                )}
              >
                <span
                  className="block w-4.5 rounded-full bg-ink"
                  style={{ height: option.dot }}
                />
              </button>
            ))}
            <div className="my-0.5 h-0.5 w-auto bg-muted" />
            <Button
              type="button"
              variant="outline"
              size="icon"
              title="Undo"
              aria-label="Undo"
              onClick={undo}
            >
              <Undo2 />
            </Button>
            <Button
              type="button"
              variant="outline"
              size="icon"
              title="Clear"
              aria-label="Clear"
              onClick={clear}
            >
              <Trash2 />
            </Button>
          </div>
        )}

        <div
          data-card-stage
          className={cn(
            "flex min-w-0 flex-col items-center gap-2",
            compact &&
              "card-creator-stage h-full min-h-0 w-full justify-center",
          )}
        >
          {/* The card being authored is a physical paper artifact: its face
              stays white and its ink tokens stay dark in both themes. */}
          <div
            className={cn(
              "card-creator-card paper-scope bg-card-face relative w-full max-w-[400px] rounded-xl border-[2.5px] border-ink p-3 shadow-[0_12px_30px_rgba(20,18,14,0.22)] sm:p-4",
              compact && "card-creator-card-compact",
            )}
          >
            <div className="bg-tape absolute -top-2.5 left-[20%] h-5 w-20 rotate-[-6deg]" />
            <div className="bg-tape absolute -top-2.5 right-[20%] h-5 w-20 rotate-[5deg]" />
            <div className="pointer-events-none absolute inset-2 rounded-lg border border-dashed border-ink/25" />

            <input
              aria-label="Card title"
              value={title}
              onChange={(e) => onTitleChange(e.target.value)}
              placeholder="Card title…"
              maxLength={60}
              className="font-hand w-full border-b-[1.5px] border-ink bg-transparent px-1.5 pb-1.5 pt-0.5 text-center text-[26px] leading-tight outline-none placeholder:text-ink/30"
            />

            <div className="relative my-3 overflow-hidden rounded-md border-[1.5px] border-ink bg-card-face">
              <canvas
                ref={canvasRef}
                tabIndex={0}
                aria-label="Card drawing canvas"
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={endStroke}
                onPointerCancel={endStroke}
                onPointerLeave={endStroke}
                className="block aspect-[6/5] h-auto w-full cursor-crosshair touch-none outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              {canvasEmpty && (
                <div className="font-hand pointer-events-none absolute inset-0 flex items-center justify-center text-xl text-ink/25">
                  draw something ✏️
                </div>
              )}
            </div>

            <textarea
              aria-label="Card rules"
              value={description}
              onChange={(e) => onDescriptionChange(e.target.value)}
              placeholder="What does this card DO?"
              rows={2}
              maxLength={500}
              className="font-hand w-full resize-none bg-transparent text-center text-[17px] text-[#333] outline-none placeholder:text-ink/30"
            />
          </div>
          {caption && (
            <p
              className={cn(
                "font-hand text-[15px] text-muted-foreground",
                compact && "sr-only",
              )}
            >
              {caption}
            </p>
          )}
        </div>

        {!compact && (
          <div
            data-card-stamps
            className="w-[190px] rounded-2xl border-[2.5px] border-ink bg-card p-3 panel-shadow"
          >
            <div className="font-marker mb-1 text-sm">Stamps</div>
            <div className="mb-2 text-[11px] font-bold text-muted-foreground">
              Tap one, then tap the card to stamp it.
            </div>
            <div className="grid grid-cols-5 gap-1.5">
              {STAMPS.map((emoji) => (
                <button
                  key={emoji}
                  type="button"
                  aria-label={`Stamp ${emoji}`}
                  aria-pressed={armedStamp === emoji}
                  onClick={() =>
                    setArmedStamp((cur) => (cur === emoji ? null : emoji))
                  }
                  className={cn(
                    "flex aspect-square cursor-pointer items-center justify-center rounded-lg border-[1.5px] border-ink bg-card text-lg",
                    armedStamp === emoji && "border-2 border-ring bg-accent",
                  )}
                >
                  {emoji}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  },
);
