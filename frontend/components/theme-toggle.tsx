"use client";

export const THEME_STORAGE_KEY = "tbwc_theme";

/**
 * Floating sticker button that flips the "dark" class on <html> and persists
 * the choice. The icon is CSS-driven (dark: variant) rather than state-driven,
 * so it never disagrees with the class the pre-paint script in layout.tsx set.
 */
export function ThemeToggle() {
  function toggle() {
    const root = document.documentElement;
    const dark = root.classList.toggle("dark");
    try {
      localStorage.setItem(THEME_STORAGE_KEY, dark ? "dark" : "light");
    } catch {
      // Storage unavailable (private mode); the toggle still applies visually.
    }
  }

  return (
    <button
      type="button"
      aria-label="Toggle light/dark theme"
      title="Toggle light/dark theme"
      onClick={toggle}
      className="fixed right-3 bottom-3 z-50 flex size-11 -rotate-3 cursor-pointer items-center justify-center rounded-full border-2 border-ink bg-card text-xl sticker-shadow-sm transition-transform hover:rotate-0 active:translate-x-px active:translate-y-px active:shadow-[2px_2px_0_var(--sticker-ink)]"
    >
      <span aria-hidden className="dark:hidden">
        ☀️
      </span>
      <span aria-hidden className="hidden dark:inline">
        🌙
      </span>
    </button>
  );
}
