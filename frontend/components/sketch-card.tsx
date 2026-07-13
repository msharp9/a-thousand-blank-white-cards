import type { CSSProperties } from "react";
import type { CardSnapshot } from "@/lib/types";
import { cn } from "@/lib/utils";

interface SketchCardProps {
  card?: CardSnapshot;
  /** Override (or supply, when no `card`) the title/rule text directly. */
  title?: string;
  description?: string;
  /** Card width in px; every other dimension derives from it. */
  w?: number;
  /** Card height in px; defaults to round(w × 1.4). */
  h?: number;
  /** Resting rotation in degrees. */
  rot?: number;
  faceDown?: boolean;
  showTape?: boolean;
  selectable?: boolean;
  selected?: boolean;
  onClick?: () => void;
  brewing?: boolean;
  artUrl?: string | null;
  className?: string;
  style?: CSSProperties;
}

/** Stable pseudo-random rotation derived from a card id, in [-max, max]. */
export function stableRotation(id: string, max = 6): number {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) | 0;
  }
  return (Math.abs(hash) % (max * 2 + 1)) - max;
}

/**
 * The hand-drawn playing card used on every card surface (hand fan, table
 * center, opponent minis, setup, epilogue, results). Sizing math follows
 * docs/design/Card.dc.html: all dimensions derive from `w`.
 */
export function SketchCard({
  card,
  title,
  description,
  w = 130,
  h,
  rot = 0,
  faceDown,
  showTape = true,
  selectable,
  selected,
  onClick,
  brewing,
  artUrl,
  className,
  style,
}: SketchCardProps) {
  const height = h ?? Math.round(w * 1.4);
  const cardTitle = title ?? card?.title ?? "";
  const cardText = description ?? card?.description ?? "";
  const isBlank = Boolean(card?.blank);
  const verdict = card?.verdict;
  const mechanicalStatus = card?.mechanical_status;
  const diagnosticLabel =
    mechanicalStatus ?? (verdict && verdict !== "ok" ? verdict : undefined);
  const diagnosticTitle = [
    card?.mechanical_reason,
    card?.correlation_id ? `Reference: ${card.correlation_id}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  const pad = Math.round(w * 0.08);
  const gap = Math.round(w * 0.035);
  const titleSize = Math.round(w * 0.115);
  const ruleSize = Math.max(9, Math.round(w * 0.082));
  const ruleMax = Math.round(height * 0.26);
  const tapeW = Math.round(w * 0.22);
  const backBig = Math.round(w * 0.28);
  const backSmall = Math.max(7, Math.round(w * 0.07));

  const tapeVisible = showTape && w >= 50;
  const tapeH = w < 70 ? 10 : 16;
  const tapeTop = w < 70 ? -5 : -8;

  return (
    <div
      className={cn(
        "relative shrink-0 overflow-hidden font-hand transition-all duration-150 [transform:rotate(var(--rot))]",
        selectable && "cursor-pointer",
        selectable &&
          !selected &&
          "hover:[transform:translateY(-24px)_rotate(0deg)]",
        selected && "[transform:translateY(-34px)_rotate(0deg)]",
        className,
      )}
      style={
        {
          ...style,
          width: w,
          height,
          "--rot": `${rot}deg`,
          background: "#ffffff",
          borderRadius: 7,
          border: "1.6px solid #1a1a1a",
          boxShadow:
            "0 8px 18px rgba(20,18,14,0.20), 0 2px 4px rgba(20,18,14,0.14)",
        } as CSSProperties
      }
      onClick={selectable ? onClick : undefined}
      role={selectable ? "button" : undefined}
      tabIndex={selectable ? 0 : undefined}
      onKeyDown={
        selectable && onClick
          ? (e) => e.key === "Enter" && onClick()
          : undefined
      }
    >
      <div
        style={{
          position: "absolute",
          inset: 4,
          border: "1px dashed rgba(26,26,26,0.28)",
          borderRadius: 5,
          pointerEvents: "none",
          zIndex: 3,
        }}
      />

      {tapeVisible && (
        <>
          <div
            className="bg-tape"
            style={{
              position: "absolute",
              top: tapeTop,
              left: "14%",
              width: tapeW,
              height: tapeH,
              transform: "rotate(-7deg)",
              boxShadow: "0 1px 2px rgba(0,0,0,0.12)",
              zIndex: 4,
            }}
          />
          <div
            className="bg-tape"
            style={{
              position: "absolute",
              top: tapeTop,
              right: "14%",
              width: tapeW,
              height: tapeH,
              transform: "rotate(6deg)",
              boxShadow: "0 1px 2px rgba(0,0,0,0.12)",
              zIndex: 4,
            }}
          />
        </>
      )}

      {faceDown ? (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background:
              "repeating-linear-gradient(45deg, #fbfbf9 0 9px, #f2f0e9 9px 18px)",
          }}
        >
          <div
            style={{
              width: "62%",
              aspectRatio: "1",
              border: "2px dashed #1a1a1a",
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              transform: "rotate(-6deg)",
              background: "#fff",
            }}
          >
            <div style={{ textAlign: "center", lineHeight: 0.9 }}>
              <div
                className="font-marker"
                style={{ fontSize: backBig, color: "#1a1a1a" }}
              >
                1K
              </div>
              <div
                style={{ fontSize: backSmall, color: "#555", letterSpacing: 1 }}
              >
                BLANK WHITE
              </div>
            </div>
          </div>
        </div>
      ) : isBlank ? (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap,
            padding: pad,
          }}
        >
          <span
            style={{
              fontSize: Math.round(w * 0.4),
              lineHeight: 1,
              opacity: 0.15,
            }}
          >
            ✏️
          </span>
          <span
            style={{
              fontSize: titleSize,
              lineHeight: 1,
              color: "rgba(26,26,26,0.35)",
            }}
          >
            Blank.
          </span>
          {selectable && (
            <span style={{ fontSize: ruleSize, color: "rgba(26,26,26,0.3)" }}>
              fill in &amp; play
            </span>
          )}
        </div>
      ) : (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            flexDirection: "column",
            padding: pad,
          }}
        >
          <div
            style={{
              fontSize: titleSize,
              color: "#1a1a1a",
              lineHeight: 1,
              borderBottom: "1.5px solid #1a1a1a",
              paddingBottom: gap,
              textAlign: "center",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {cardTitle || "Untitled"}
          </div>
          {artUrl ? (
            <>
              <div
                style={{
                  flex: 1,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  minHeight: 0,
                  padding: `${gap}px 0`,
                }}
              >
                {/* Dynamic backend endpoint with immutable cache headers; the
                    browser cache does the work — next/image adds nothing here. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={artUrl}
                  alt=""
                  style={{
                    maxWidth: "100%",
                    maxHeight: "100%",
                    objectFit: "contain",
                  }}
                />
              </div>
              <div
                style={{
                  fontSize: ruleSize,
                  color: "#333",
                  lineHeight: 1.05,
                  textAlign: "center",
                  maxHeight: ruleMax,
                  overflow: "hidden",
                }}
              >
                {cardText}
              </div>
            </>
          ) : (
            <div
              style={{
                flex: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                minHeight: 0,
                padding: `${gap}px 0`,
              }}
            >
              <div
                style={{
                  fontSize: ruleSize,
                  color: "#333",
                  lineHeight: 1.05,
                  textAlign: "center",
                  maxHeight: "100%",
                  overflow: "hidden",
                }}
              >
                {cardText}
              </div>
            </div>
          )}
        </div>
      )}

      {!faceDown && diagnosticLabel && (
        <div
          title={diagnosticTitle || undefined}
          style={{
            position: "absolute",
            top: Math.round(w * 0.06),
            right: Math.round(w * 0.05),
            padding: "0 4px",
            fontSize: Math.max(8, Math.round(w * 0.075)),
            lineHeight: 1.4,
            border: "1px solid #1a1a1a",
            borderRadius: 3,
            background: "#efe9da",
            color: "#1a1a1a",
            transform: "rotate(-8deg)",
            zIndex: 5,
            boxShadow: "0 1px 2px rgba(0,0,0,0.15)",
          }}
        >
          {diagnosticLabel}
        </div>
      )}

      {brewing && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 6,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 2,
            background: "rgba(255,255,255,0.75)",
          }}
        >
          <span
            className="animate-wig"
            style={{ fontSize: Math.round(w * 0.22), lineHeight: 1 }}
          >
            ✏️
          </span>
          <span
            style={{
              fontSize: Math.max(9, Math.round(w * 0.09)),
              color: "#333",
            }}
          >
            interpreting…
          </span>
        </div>
      )}
    </div>
  );
}
