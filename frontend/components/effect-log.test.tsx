import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EffectLog } from "./effect-log";

describe("EffectLog", () => {
  it("shows the empty state when there is nothing to log", () => {
    render(<EffectLog log={[]} brewing={null} />);
    expect(screen.getByText(/no cards played yet/i)).toBeTruthy();
  });

  it("renders newest entry first", () => {
    render(
      <EffectLog
        log={["Alice played Zap", "Bob played Nuh-Uh"]}
        brewing={null}
      />,
    );
    const entries = screen.getAllByText(/played/);
    expect(entries[0]).toHaveTextContent("Bob played Nuh-Uh");
    expect(entries[1]).toHaveTextContent("Alice played Zap");
  });

  it("distinguishes arbiter commentary from mechanical entries", () => {
    render(
      <EffectLog
        log={["Alice played Zap", "🤖 That felt oddly personal."]}
        brewing={null}
      />,
    );
    const arbiterEntry = screen.getByText(/that felt oddly personal/i);
    expect(arbiterEntry).toHaveAttribute("data-arbiter", "true");
    const mechanicalEntry = screen.getByText("Alice played Zap");
    expect(mechanicalEntry).not.toHaveAttribute("data-arbiter");
  });

  it("shows the brewing indicator above the log entries", () => {
    render(<EffectLog log={["Alice played Zap"]} brewing="Interpreting Zap" />);
    expect(screen.getByText(/interpreting card/i)).toBeTruthy();
  });

  it("does not show the empty state while brewing", () => {
    render(<EffectLog log={[]} brewing="Interpreting Zap" />);
    expect(screen.queryByText(/no cards played yet/i)).toBeNull();
  });
});
