"use client";

import {
  useCallback,
  useEffect,
  useRef,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";

export const drawingSurfaceStyle: CSSProperties = {
  touchAction: "none",
  overscrollBehavior: "contain",
  userSelect: "none",
  WebkitUserSelect: "none",
};

/**
 * Hardens a freehand drawing surface against mobile page gestures. Tracks a
 * single active pointer so secondary fingers are ignored, prevents default on
 * accepted pointer events, and holds pointer capture for the stroke.
 *
 * - `capturePointer`: call on pointer-down; false when another pointer owns
 *   the surface (the event should be ignored).
 * - `movePointer`: call on pointer-move; false for non-active pointers.
 * - `releasePointer`: call on pointer-up/cancel/lost-capture; false for
 *   non-active pointers.
 */
export function useDrawingSurface<T extends Element>(
  surfaceRef: RefObject<T | null>,
) {
  const activePointerRef = useRef<number | null>(null);

  // React registers touch listeners passively, so scroll/pull-to-refresh must
  // be blocked with a native non-passive touchmove listener on the surface.
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    const blockTouchScroll = (event: Event) => {
      if (event.cancelable) event.preventDefault();
    };
    surface.addEventListener("touchmove", blockTouchScroll, {
      passive: false,
    });
    return () => surface.removeEventListener("touchmove", blockTouchScroll);
  }, [surfaceRef]);

  const capturePointer = useCallback((event: ReactPointerEvent<T>) => {
    if (activePointerRef.current !== null) return false;
    event.preventDefault();
    activePointerRef.current = event.pointerId;
    if (typeof event.currentTarget.setPointerCapture === "function") {
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    return true;
  }, []);

  const movePointer = useCallback((event: ReactPointerEvent<T>) => {
    if (activePointerRef.current !== event.pointerId) return false;
    event.preventDefault();
    return true;
  }, []);

  const releasePointer = useCallback((event: ReactPointerEvent<T>) => {
    if (activePointerRef.current !== event.pointerId) return false;
    activePointerRef.current = null;
    const target = event.currentTarget;
    if (
      typeof target.hasPointerCapture === "function" &&
      typeof target.releasePointerCapture === "function" &&
      target.hasPointerCapture(event.pointerId)
    ) {
      target.releasePointerCapture(event.pointerId);
    }
    return true;
  }, []);

  return { capturePointer, movePointer, releasePointer };
}
