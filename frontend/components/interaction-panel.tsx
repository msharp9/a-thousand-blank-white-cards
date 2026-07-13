"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { EyeOffIcon, RotateCcwIcon, Undo2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type {
  CardSnapshot,
  DrawingPoint,
  DrawingStroke,
  InteractionDescriptor,
  InteractionProgressMsg,
  InteractionRequestMsg,
  InteractionResponsePayload,
  PendingInteractionSummary,
} from "@/lib/types";

// Leave room for the backend's typed JSON reserialization: JS emits edge
// coordinates as `0`/`1`, while Pydantic emits `0.0`/`1.0` before enforcing its
// 65 KiB cap. 48 KiB keeps the post-parse representation safely below it.
const DRAWING_WIRE_BYTES = 48 * 1024;
const DEFAULT_COLOR = "#1a1a1a";
const DEFAULT_WIDTH = 0.01;

function encodedBytes(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

function clampUnit(value: number): number {
  return Math.max(0, Math.min(1, value));
}

/** Normalize untrusted/local vector input to the exact bounded wire shape. */
export function normalizeDrawingResponse(
  value: unknown,
  maxStrokes = 64,
  maxPointsPerStroke = 256,
): DrawingStroke[] {
  if (!Array.isArray(value)) return [];
  const strokes: DrawingStroke[] = [];
  for (const candidate of value.slice(0, Math.max(0, maxStrokes))) {
    if (!candidate || typeof candidate !== "object") continue;
    const raw = candidate as Record<string, unknown>;
    if (!Array.isArray(raw.points)) continue;
    const points: DrawingPoint[] = [];
    for (const point of raw.points.slice(0, Math.max(0, maxPointsPerStroke))) {
      if (!point || typeof point !== "object") continue;
      const x = Number((point as Record<string, unknown>).x);
      const y = Number((point as Record<string, unknown>).y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      points.push({ x: clampUnit(x), y: clampUnit(y) });
    }
    if (!points.length) continue;
    const color =
      typeof raw.color === "string" && /^#[0-9A-Fa-f]{6}$/.test(raw.color)
        ? raw.color
        : DEFAULT_COLOR;
    const candidateWidth = Number(raw.width);
    const width = Number.isFinite(candidateWidth)
      ? Math.max(0.001, Math.min(0.1, candidateWidth))
      : DEFAULT_WIDTH;
    strokes.push({ color, width, points });
  }

  // The geometry caps are intentionally larger than the encoded-response cap.
  // Coarsen the longest tail strokes until the final JSON is safe to send.
  while (
    strokes.length &&
    encodedBytes({ kind: "drawing", strokes }) > DRAWING_WIRE_BYTES
  ) {
    const longest = strokes.reduce(
      (best, stroke, index) =>
        stroke.points.length > strokes[best].points.length ? index : best,
      0,
    );
    if (strokes[longest].points.length <= 1) {
      strokes.pop();
    } else {
      strokes[longest] = {
        ...strokes[longest],
        points: strokes[longest].points.filter((_, index) => index % 2 === 0),
      };
    }
  }
  return strokes;
}

export function drawingStrokesFromPayload(payload: unknown): DrawingStroke[] {
  return normalizeDrawingResponse(payload, 64, 256);
}

function DrawingPreview({ payload }: { payload: unknown }) {
  const strokes = useMemo(() => drawingStrokesFromPayload(payload), [payload]);
  if (!strokes.length) return null;
  return (
    <svg
      viewBox="0 0 100 100"
      role="img"
      aria-label="Drawing submission"
      className="h-28 w-full rounded-lg border border-ink/30 bg-card-face"
    >
      {strokes.map((stroke, index) => (
        <polyline
          key={index}
          points={stroke.points
            .map((point) => `${point.x * 100},${point.y * 100}`)
            .join(" ")}
          fill="none"
          stroke={stroke.color}
          strokeWidth={Math.max(1, stroke.width * 100)}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </svg>
  );
}

function DrawingInput({
  descriptor,
  disabled,
  onSubmit,
}: {
  descriptor: InteractionDescriptor;
  disabled: boolean;
  onSubmit: (payload: InteractionResponsePayload) => void;
}) {
  const requestedStrokes = Number(descriptor.max_strokes);
  const requestedPoints = Number(descriptor.max_points_per_stroke);
  const maxStrokes = Number.isFinite(requestedStrokes)
    ? Math.min(64, Math.max(1, requestedStrokes))
    : 64;
  const maxPoints = Math.min(
    256,
    Math.max(2, Number.isFinite(requestedPoints) ? requestedPoints : 256),
  );
  const [strokes, setStrokes] = useState<DrawingStroke[]>([]);
  const drawingRef = useRef(false);

  const pointFor = useCallback((event: ReactPointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    if (!box.width || !box.height) return null;
    return {
      x: clampUnit((event.clientX - box.left) / box.width),
      y: clampUnit((event.clientY - box.top) / box.height),
    };
  }, []);

  const startStroke = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (disabled || strokes.length >= maxStrokes) return;
    const point = pointFor(event);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drawingRef.current = true;
    setStrokes((current) => [
      ...current,
      { color: DEFAULT_COLOR, width: DEFAULT_WIDTH, points: [point] },
    ]);
  };

  const extendStroke = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!drawingRef.current) return;
    const point = pointFor(event);
    if (!point) return;
    setStrokes((current) => {
      const last = current[current.length - 1];
      if (!last || last.points.length >= maxPoints) return current;
      return [
        ...current.slice(0, -1),
        { ...last, points: [...last.points, point] },
      ];
    });
  };

  const finishStroke = () => {
    drawingRef.current = false;
  };

  return (
    <div className="flex flex-col gap-3">
      <svg
        viewBox="0 0 100 100"
        role="img"
        aria-label="Drawing canvas"
        className="aspect-[4/3] w-full touch-none rounded-xl border-2 border-ink bg-card-face shadow-inner"
        onPointerDown={startStroke}
        onPointerMove={extendStroke}
        onPointerUp={finishStroke}
        onPointerCancel={finishStroke}
      >
        {strokes.map((stroke, index) => (
          <polyline
            key={index}
            points={stroke.points
              .map((point) => `${point.x * 100},${point.y * 100}`)
              .join(" ")}
            fill="none"
            stroke={stroke.color}
            strokeWidth={Math.max(1, stroke.width * 100)}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ))}
      </svg>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={disabled || !strokes.length}
          onClick={() => setStrokes((current) => current.slice(0, -1))}
        >
          <Undo2Icon /> Undo
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || !strokes.length}
          onClick={() => setStrokes([])}
        >
          <RotateCcwIcon /> Clear
        </Button>
        <Button
          type="button"
          className="ml-auto"
          disabled={disabled || !strokes.length}
          onClick={() =>
            onSubmit({
              kind: "drawing",
              strokes: normalizeDrawingResponse(strokes, maxStrokes, maxPoints),
            })
          }
        >
          Submit drawing
        </Button>
      </div>
      <p className="font-hand text-sm text-muted-foreground">
        Vector strokes only · {strokes.length}/{maxStrokes} strokes
      </p>
    </div>
  );
}

function InteractionForm({
  request,
  cards,
  disabled,
  onSubmit,
}: {
  request: InteractionRequestMsg;
  cards: Record<string, CardSnapshot>;
  disabled: boolean;
  onSubmit: (payload: InteractionResponsePayload) => void;
}) {
  const descriptor = request.descriptor;
  const [selected, setSelected] = useState<string[]>([]);
  const [numberValue, setNumberValue] = useState("");
  const [textValue, setTextValue] = useState("");

  switch (descriptor.kind) {
    case "choice": {
      const options = Array.isArray(descriptor.options)
        ? descriptor.options
        : [];
      const minimum = descriptor.min_selections ?? 1;
      const maximum = descriptor.max_selections ?? 1;
      const toggle = (id: string) => {
        setSelected((current) => {
          if (current.includes(id))
            return current.filter((value) => value !== id);
          return current.length < maximum ? [...current, id] : current;
        });
      };
      if (!options.length) {
        return (
          <p
            role="status"
            className="rounded-xl border-2 border-dashed border-ink/40 bg-card p-4 font-hand"
          >
            No choices are available. The server will resolve this card safely.
          </p>
        );
      }
      return (
        <div className="grid gap-2 sm:grid-cols-2">
          {options.map((option) => {
            const active = selected.includes(option.id);
            return (
              <button
                type="button"
                key={option.id}
                aria-pressed={active}
                disabled={disabled}
                onClick={() => toggle(option.id)}
                className={`rounded-xl border-2 p-3 text-left font-hand text-lg transition ${
                  active
                    ? "border-primary bg-primary/10"
                    : "border-ink bg-card hover:bg-accent/30"
                }`}
              >
                <DrawingPreview payload={option.payload} />
                <span className="mt-1 block">{option.label}</span>
              </button>
            );
          })}
          <Button
            type="button"
            className="sm:col-span-2"
            disabled={
              disabled || selected.length < minimum || selected.length > maximum
            }
            onClick={() => onSubmit({ kind: "choice", option_ids: selected })}
          >
            Submit choice
          </Button>
        </div>
      );
    }
    case "number": {
      const value = Number(numberValue);
      const minimum = descriptor.minimum ?? -1_000_000;
      const maximum = descriptor.maximum ?? 1_000_000;
      const valid =
        numberValue.trim() !== "" &&
        Number.isFinite(value) &&
        value >= minimum &&
        value <= maximum &&
        (!descriptor.integer || Number.isInteger(value));
      return (
        <div className="flex flex-col gap-3">
          <Input
            type="number"
            value={numberValue}
            min={minimum}
            max={maximum}
            step={descriptor.integer ? 1 : "any"}
            disabled={disabled}
            onChange={(event) => setNumberValue(event.target.value)}
          />
          <Button
            disabled={disabled || !valid}
            onClick={() => onSubmit({ kind: "number", value })}
          >
            Submit number
          </Button>
        </div>
      );
    }
    case "text": {
      const maxLength = Math.min(
        2000,
        Math.max(1, descriptor.max_length ?? 500),
      );
      return (
        <div className="flex flex-col gap-3">
          <Textarea
            value={textValue}
            maxLength={maxLength}
            disabled={disabled}
            onChange={(event) =>
              setTextValue(event.target.value.slice(0, maxLength))
            }
          />
          <div className="flex items-center justify-between gap-3">
            <span className="font-hand text-sm text-muted-foreground">
              {textValue.length}/{maxLength}
            </span>
            <Button
              disabled={disabled}
              onClick={() => onSubmit({ kind: "text", value: textValue })}
            >
              Submit text
            </Button>
          </div>
        </div>
      );
    }
    case "card_pick": {
      const cardIds = Array.isArray(descriptor.card_ids)
        ? descriptor.card_ids
        : [];
      return cardIds.length ? (
        <div className="grid gap-2 sm:grid-cols-2">
          {cardIds.map((cardId) => (
            <Button
              type="button"
              variant="outline"
              key={cardId}
              disabled={disabled}
              onClick={() => onSubmit({ kind: "card_pick", card_id: cardId })}
            >
              {cards[cardId]?.title || cardId}
            </Button>
          ))}
        </div>
      ) : (
        <p
          role="status"
          className="rounded-xl border-2 border-dashed border-ink/40 bg-card p-4 font-hand"
        >
          No cards are available to pick. The server will resolve this card
          safely.
        </p>
      );
    }
    case "confirm":
      return (
        <div className="flex flex-wrap gap-3">
          <Button
            disabled={disabled}
            onClick={() => onSubmit({ kind: "confirm", confirmed: true })}
          >
            {descriptor.confirm_label || "Yes"}
          </Button>
          <Button
            variant="outline"
            disabled={disabled}
            onClick={() => onSubmit({ kind: "confirm", confirmed: false })}
          >
            {descriptor.decline_label || "No"}
          </Button>
        </div>
      );
    case "drawing":
      return (
        <DrawingInput
          descriptor={descriptor}
          disabled={disabled}
          onSubmit={onSubmit}
        />
      );
    default:
      return (
        <div
          role="status"
          className="rounded-xl border-2 border-dashed border-ink/40 bg-card p-4"
        >
          <p className="font-hand text-lg">
            This interaction needs a newer game client.
          </p>
          <p className="font-mono text-xs text-muted-foreground">
            Unknown kind: {descriptor.kind}
          </p>
        </div>
      );
  }
}

export function InteractionPanel({
  pending,
  request,
  progressMessage,
  cards,
  onSubmit,
}: {
  pending: PendingInteractionSummary | null | undefined;
  request: InteractionRequestMsg | null;
  progressMessage: InteractionProgressMsg | null;
  cards: Record<string, CardSnapshot>;
  onSubmit: (
    interactionId: string,
    payload: InteractionResponsePayload,
  ) => void;
}) {
  const interactionId = pending?.interaction_id ?? request?.interaction_id;
  const activeRequest =
    request?.interaction_id === interactionId ? request : null;
  const activeProgress =
    progressMessage?.interaction_id === interactionId ? progressMessage : null;
  const deadline =
    activeProgress?.deadline_at ??
    activeRequest?.deadline_at ??
    pending?.deadline_at;
  const progress =
    activeProgress?.progress ?? activeRequest?.progress ?? pending?.progress;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!interactionId || !deadline) return;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [interactionId, deadline]);

  if (!interactionId) return null;
  const secondsLeft = deadline
    ? Math.max(0, Math.ceil((new Date(deadline).getTime() - now) / 1000))
    : null;
  const expired = secondsLeft === 0;
  const submitted = Boolean(progress?.submitted);

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-[rgba(20,18,14,0.45)] p-4 backdrop-blur-[2px]">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="interaction-title"
        className="max-h-[92dvh] w-full max-w-xl overflow-y-auto -rotate-[0.3deg] rounded-2xl border-[3px] border-ink bg-panel-paper p-5 sticker-shadow"
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 id="interaction-title" className="font-marker text-2xl">
                Card interaction
              </h2>
              {activeRequest?.descriptor.sealed && (
                <span className="inline-flex items-center gap-1 rounded-full border border-ink/30 bg-card px-2 py-0.5 font-hand text-sm">
                  <EyeOffIcon className="size-3.5" /> Sealed
                </span>
              )}
            </div>
            <p className="mt-1 font-hand text-xl">
              {activeRequest?.descriptor.prompt || "Waiting for the table…"}
            </p>
          </div>
          <div className="shrink-0 text-right font-hand">
            <p
              className={expired ? "text-destructive" : "text-muted-foreground"}
            >
              {secondsLeft === null ? "No timer" : `${secondsLeft}s`}
            </p>
            <p className="text-sm text-muted-foreground">
              {progress?.received_count ?? 0}/{progress?.expected_count ?? 0}{" "}
              submitted
            </p>
          </div>
        </div>

        {submitted || !activeRequest || expired ? (
          <div
            role="status"
            className="rounded-xl border-2 border-dashed border-ink/40 bg-card p-5 text-center"
          >
            <p className="font-hand text-xl">
              {expired
                ? "Time’s up — resolving the card…"
                : "Answer locked in."}
            </p>
            <p className="font-hand text-base text-muted-foreground">
              Waiting for the rest of the table. Sealed answers stay hidden
              until the barrier closes.
            </p>
          </div>
        ) : (
          <InteractionForm
            key={activeRequest.interaction_id}
            request={activeRequest}
            cards={cards}
            disabled={expired}
            onSubmit={(payload) => onSubmit(interactionId, payload)}
          />
        )}
      </section>
    </div>
  );
}
