"use client";

import { useCallback, useSyncExternalStore } from "react";

export const COMPACT_VIEWPORT_QUERY =
  "(max-width: 639px), (max-height: 500px) and (pointer: coarse)";

export function useCompactViewport(): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    if (typeof window.matchMedia !== "function") return () => {};
    const media = window.matchMedia(COMPACT_VIEWPORT_QUERY);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  return useSyncExternalStore(
    subscribe,
    () =>
      typeof window.matchMedia === "function"
        ? window.matchMedia(COMPACT_VIEWPORT_QUERY).matches
        : false,
    () => false,
  );
}
