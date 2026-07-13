// Drag-and-drop card play: pure logic for resolving a dnd-kit drop into a
// `play` message. Dropping on the felt is a general play (same as the Play
// button); dropping on an opponent seat plays the card targeting that player —
// PlayMsg.chosen_player_id is carried end-to-end by the server, which skips
// its prompt_choice follow-up when the target is already supplied.

import type { CardSnapshot, ClientMsg } from "@/lib/types";

export const FELT_DROP_ID = "felt";

export function seatDropId(playerId: string): string {
  return `seat:${playerId}`;
}

export type DropTargetData =
  { type: "felt" } | { type: "seat"; playerId: string };

export interface DropPlay {
  cardId: string;
  /** Seat drops target this player; felt drops leave it null. */
  targetPlayerId: string | null;
}

/** What a completed drag should do: send a play now, open the author-on-play
 * dialog for a blank, or nothing (missed / unknown drop target). */
export type DropAction =
  { kind: "play"; msg: ClientMsg } | { kind: "author"; drop: DropPlay } | null;

interface DragEndLike {
  active: { id: string | number };
  over: {
    id: string | number;
    data: { current?: Record<string, unknown> | undefined };
  } | null;
}

export function resolveDropPlay(event: DragEndLike): DropPlay | null {
  const { active, over } = event;
  if (!over) return null;
  const cardId = String(active.id);
  if (over.id === FELT_DROP_ID) return { cardId, targetPlayerId: null };
  const data = over.data.current;
  if (data?.type === "seat" && typeof data.playerId === "string") {
    return { cardId, targetPlayerId: data.playerId };
  }
  return null;
}

export function playMessage(
  drop: DropPlay,
  authored?: { title: string; description: string; art?: string },
): ClientMsg {
  return {
    type: "play",
    card_id: drop.cardId,
    ...(drop.targetPlayerId ? { chosen_player_id: drop.targetPlayerId } : {}),
    ...(authored
      ? {
          title: authored.title,
          description: authored.description,
          ...(authored.art ? { art: authored.art } : {}),
        }
      : {}),
  };
}

export function planDrop(
  event: DragEndLike,
  cards: Record<string, CardSnapshot>,
): DropAction {
  const drop = resolveDropPlay(event);
  if (!drop) return null;
  if (cards[drop.cardId]?.blank) return { kind: "author", drop };
  return { kind: "play", msg: playMessage(drop) };
}

export function executeDrop(
  action: DropAction,
  send: (msg: ClientMsg) => void,
  openAuthor: (drop: DropPlay) => void,
): void {
  if (!action) return;
  if (action.kind === "play") send(action.msg);
  else openAuthor(action.drop);
}
