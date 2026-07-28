import { conditionName } from "@/lib/conditions";
import type {
  EndConditionSnapshot,
  RulesSnapshot,
  WinConditionSnapshot,
} from "@/lib/types";

function cards(count: number): string {
  return `${count} card${count === 1 ? "" : "s"}`;
}

function points(count: number): string {
  return `${count} point${count === 1 ? "" : "s"}`;
}

const SNAKE_CASE = /^[a-z0-9]+(?:[_:.-][a-z0-9]+)+$/i;

export function humanizeRuleValue(value: unknown): string {
  if (typeof value === "string") {
    return SNAKE_CASE.test(value) ? conditionName(value) : value;
  }
  if (Array.isArray(value)) {
    return value.map(humanizeRuleValue).join(", ");
  }
  if (value !== null && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(
        ([key, entry]) => `${conditionName(key)}: ${humanizeRuleValue(entry)}`,
      )
      .join(", ");
  }
  return String(value);
}

function ruleEntryPhrase(key: string, value: unknown): string {
  const label = conditionName(key);
  if (value === true) return label;
  return `${label} (${humanizeRuleValue(value)})`;
}

function meaningfulEntries(bag: Record<string, unknown>): [string, unknown][] {
  return Object.entries(bag).filter(([, value]) => {
    if (value === false || value == null) return false;
    if (value === "") return false;
    if (Array.isArray(value) && value.length === 0) return false;
    return true;
  });
}

export function drawRuleSentence(draw: number): string {
  if (draw === 0) return "No cards are drawn at the start of your turn.";
  return `Draw ${cards(draw)} at the start of your turn.`;
}

export function playRuleSentence(play: number): string {
  if (play === 0) return "No cards may be played on your turn.";
  return `Play ${cards(play)} on your turn.`;
}

export function cannotPlaySentences(
  cannotPlay: Record<string, unknown>,
): string[] {
  return meaningfulEntries(cannotPlay)
    .filter(([key, value]) => !(key === "draw" && value === 0))
    .map(([key, value]) => {
      if (key === "draw" && typeof value === "number") {
        return `If you cannot play, draw ${cards(value)}.`;
      }
      return `If you cannot play: ${ruleEntryPhrase(key, value)}.`;
    });
}

export function endConditionSentence(end: EndConditionSnapshot): string {
  switch (end.type) {
    case "deck_empty":
      return "Game ends when the deck runs out, after the current turn.";
    case "empty_hand":
      return "Game ends when a player runs out of cards in hand.";
    case "points_reached":
      return end.threshold == null
        ? "Game ends when a player reaches the target score."
        : `Game ends when a player reaches ${points(end.threshold)}.`;
    case "now":
      return "The game is ending now.";
    default:
      return `Game ends: ${ruleEntryPhrase(end.type, end.threshold ?? true)}.`;
  }
}

export function winConditionSentence(win: WinConditionSnapshot): string {
  switch (win.kind) {
    case "highest_points":
      return "Winner: highest score.";
    case "lowest_points":
      return "Winner: lowest score.";
    case "first_to":
      return win.threshold == null
        ? "Winner: first to reach the target score."
        : `Winner: first to ${points(win.threshold)}.`;
    case "empty_hand":
      return "Winner: first to empty their hand.";
    case "last_standing":
      return "Winner: last player standing.";
    case "none":
      return "Winner: no one — the game simply ends.";
    default:
      return `Winner: ${ruleEntryPhrase(win.kind, win.threshold ?? true)}.`;
  }
}

export function handLimitSentence(limit: number): string {
  return `Hand limit: ${cards(limit)}.`;
}

export function turnTimerSentence(seconds: number): string {
  return `Each turn has a ${seconds}-second time limit.`;
}

export function skipPredicateSentence(name: string): string {
  return `Some turns may be skipped (${conditionName(name)}).`;
}

export function extraRuleSentences(extra: Record<string, unknown>): string[] {
  return meaningfulEntries(extra).map(([key, value]) => {
    if (value === true) return `${conditionName(key)} is in effect.`;
    return `${conditionName(key)}: ${humanizeRuleValue(value)}.`;
  });
}

export function coreRuleSentences(rules: RulesSnapshot): string[] {
  const sentences = [
    drawRuleSentence(rules.draw),
    playRuleSentence(rules.play),
    ...cannotPlaySentences(rules.cannot_play ?? {}),
    endConditionSentence(rules.end_condition),
    winConditionSentence(rules.win_condition),
  ];
  if (rules.hand_limit != null)
    sentences.push(handLimitSentence(rules.hand_limit));
  if (rules.turn_timer != null)
    sentences.push(turnTimerSentence(rules.turn_timer));
  if (rules.skip_predicate) {
    sentences.push(skipPredicateSentence(rules.skip_predicate));
  }
  sentences.push(...extraRuleSentences(rules.extra ?? {}));
  return sentences;
}
