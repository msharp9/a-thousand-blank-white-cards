import type { DiceRollMsg, ReactionResultMsg } from "@/lib/types";

export const ARBITER_PREFIX = "🤖 ";

export type ViewportNotice =
  | {
      id: string;
      lane: "top";
      kind: "error";
      message: string;
      timeoutMs: 4500;
    }
  | {
      id: string;
      lane: "top";
      kind: "reaction";
      result: ReactionResultMsg;
      timeoutMs: 4000;
    }
  | {
      id: string;
      lane: "top";
      kind: "dice";
      roll: DiceRollMsg;
      timeoutMs: 5000;
    }
  | {
      id: string;
      lane: "top";
      kind: "admin";
      message: string;
      outcome: "applied" | "rejected" | "expired" | "cancelled";
      timeoutMs: 5000;
    }
  | {
      id: string;
      lane: "arbiter";
      kind: "arbiter";
      message: string;
      timeoutMs: 7000;
    };

export type NoticeLane = ViewportNotice["lane"];

export function parseArbiterLogEntry(entry: string): string | null {
  if (!entry.startsWith(ARBITER_PREFIX)) return null;
  const message = entry.slice(ARBITER_PREFIX.length).trim();
  return message || null;
}

export function enqueueNotice(
  queue: ViewportNotice[],
  notice: ViewportNotice,
): ViewportNotice[] {
  return [...queue, notice];
}

export function dismissNotice(
  queue: ViewportNotice[],
  id: string,
): ViewportNotice[] {
  return queue.filter((notice) => notice.id !== id);
}
