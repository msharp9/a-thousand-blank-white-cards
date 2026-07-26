import type { CardSnapshot, HookSnapshot } from "@/lib/types";

export function normalizeConditionKey(key: string): string {
  return key.trim().toLocaleLowerCase();
}

export function conditionName(key: string): string {
  return normalizeConditionKey(key)
    .replace(/[_:.-]+/g, " ")
    .replace(/\b\p{L}/gu, (letter) => letter.toLocaleUpperCase());
}

export function activeConditions(
  conditions: Record<string, unknown> | undefined,
): [string, unknown][] {
  return Object.entries(conditions ?? {})
    .filter(([, value]) => Boolean(value))
    .sort(([left], [right]) =>
      conditionName(left).localeCompare(conditionName(right)),
    );
}

export function conditionDuration(ttl: number | undefined): string {
  if (ttl === undefined) return "";
  if (ttl === 0) return "for the rest of this turn";
  return `for ${ttl} more turn${ttl === 1 ? "" : "s"}`;
}

export function conditionSentence(
  playerName: string,
  key: string,
  ttl?: number,
): string {
  const duration = conditionDuration(ttl);
  return `${playerName} is ${conditionName(key)}${duration ? ` ${duration}` : ""}`;
}

export function conditionValueDetail(value: unknown): string | null {
  if (typeof value === "boolean" || value == null) return null;
  if (typeof value === "number") {
    return `${value} stack${value === 1 ? "" : "s"}`;
  }
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, detail]) => `${conditionName(key)}: ${String(detail)}`)
      .join(" · ");
  }
  return String(value);
}

const HOOK_EVENT_FALLBACKS: Record<string, string> = {
  on_play: "A standing rule applies whenever a card is played.",
  on_validate_play: "A standing rule determines which cards may be played.",
  on_score_change: "A standing rule applies whenever a score changes.",
  on_turn_start: "A standing rule applies at the start of each turn.",
  on_turn_end: "A standing rule applies at the end of each turn.",
  on_draw_step: "A standing rule applies whenever a player draws.",
  on_win_check: "A standing rule applies when the game checks for a winner.",
  on_game_end: "A standing rule applies when the game ends.",
};

export function hookTitle(
  hook: HookSnapshot,
  cards: Record<string, CardSnapshot>,
): string {
  const generated = hook.title?.trim();
  if (generated) return generated;
  const source = cards[hook.source_card_id];
  const description = source?.description?.trim();
  if (description) return description;
  if (source?.title?.trim()) return `${source.title} remains in effect.`;
  return (
    HOOK_EVENT_FALLBACKS[hook.event] ??
    "A standing rule is currently in effect."
  );
}
