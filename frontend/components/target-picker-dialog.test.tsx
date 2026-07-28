import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TargetPickerDialog } from "./target-picker-dialog";
import type { CardSnapshot, PromptChoiceMsg } from "@/lib/types";

function prompt(overrides: Partial<PromptChoiceMsg> = {}): PromptChoiceMsg {
  return {
    type: "prompt_choice",
    card_id: "played",
    prompt: "Steal which card?",
    choices: [],
    ...overrides,
  };
}

const stateCards: Record<string, CardSnapshot> = {
  played: { id: "played", title: "Grand Theft", description: "Steal a card." },
};

function renderDialog(
  promptMsg: PromptChoiceMsg | null,
  onPick = vi.fn(),
  onCancel = vi.fn(),
) {
  render(
    <TargetPickerDialog
      prompt={promptMsg}
      playedTitle="Grand Theft"
      players={[{ id: "p1" }, { id: "p2" }]}
      cards={stateCards}
      roomCode="ROOM01"
      onPick={onPick}
      onCancel={onCancel}
    />,
  );
  return { onPick, onCancel };
}

describe("TargetPickerDialog", () => {
  it("renders nothing without a prompt", () => {
    renderDialog(null);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps player targets as avatar-colored buttons", async () => {
    const user = userEvent.setup();
    const { onPick } = renderDialog(
      prompt({
        prompt: "Choose a victim",
        choices: [
          { player_id: "p2", name: "Bob" },
          { player_id: "p1", name: "Alice" },
        ],
      }),
    );
    expect(screen.getByText("Choose a victim")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Choose / })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Bob" }));
    expect(onPick).toHaveBeenCalledWith({ player_id: "p2", name: "Bob" });
  });

  it("renders card targets as full faces from the prompt snapshots", async () => {
    const user = userEvent.setup();
    const { onPick } = renderDialog(
      prompt({
        choices: [
          { card_id: "v1", name: "Hidden Gem" },
          { card_id: "v2", name: "Faceless" },
        ],
        cards: {
          v1: {
            id: "v1",
            title: "Hidden Gem",
            description: "A secret rule only the victim knew.",
          },
        },
      }),
    );
    expect(
      screen.getByText("A secret rule only the victim knew."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Choose Hidden Gem" }));
    expect(onPick).toHaveBeenCalledWith({ card_id: "v1", name: "Hidden Gem" });
  });

  it("shows an accessible fallback face when a snapshot is missing", () => {
    renderDialog(prompt({ choices: [{ card_id: "v2", name: "Faceless" }] }));
    expect(
      screen.getByRole("button", { name: "Choose Faceless" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Card details unavailable")).toBeInTheDocument();
    expect(screen.queryByText("v2")).toBeNull();
  });

  it("cancels the pending play", async () => {
    const user = userEvent.setup();
    const { onCancel } = renderDialog(
      prompt({ choices: [{ player_id: "p1", name: "Alice" }] }),
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });
});
