import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { THEME_STORAGE_KEY, ThemeToggle } from "./theme-toggle";

afterEach(() => {
  document.documentElement.classList.remove("dark");
  localStorage.removeItem(THEME_STORAGE_KEY);
});

describe("ThemeToggle", () => {
  it("adds the dark class and persists the choice", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    await user.click(
      screen.getByRole("button", { name: /toggle light\/dark theme/i }),
    );
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("toggles back to light and persists that too", async () => {
    const user = userEvent.setup();
    document.documentElement.classList.add("dark");
    render(<ThemeToggle />);
    await user.click(
      screen.getByRole("button", { name: /toggle light\/dark theme/i }),
    );
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });
});
