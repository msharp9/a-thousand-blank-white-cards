"use client";

import { useCallback, useSyncExternalStore } from "react";

export const COMPACT_VIEWPORT_QUERY =
  "(max-width: 639px), (max-height: 500px) and (pointer: coarse)";

export const COMPACT_AUTHORING_QUERY =
  "(max-width: 767px), (max-height: 699px)";

export const WIDE_GAME_VIEW_QUERY = "(min-width: 1280px)";

function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window.matchMedia !== "function") return () => {};
      const media = window.matchMedia(query);
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    },
    [query],
  );

  return useSyncExternalStore(
    subscribe,
    () =>
      typeof window.matchMedia === "function"
        ? window.matchMedia(query).matches
        : false,
    () => false,
  );
}

export function useCompactViewport(): boolean {
  return useMediaQuery(COMPACT_VIEWPORT_QUERY);
}

export function useCompactAuthoringViewport(): boolean {
  return useMediaQuery(COMPACT_AUTHORING_QUERY);
}

export function useWideGameView(): boolean {
  return useMediaQuery(WIDE_GAME_VIEW_QUERY);
}
