// TypeScript mirrors of the backend WS envelopes (tbwc/models/ws_messages.py).

// ─── client → server ──────────────────────────────────────────────────────

export type Placement = {
  zone: "self" | "player" | "center";
  target_player_id?: string;
};

export type JoinMsg = { type: "join"; player_id: string | null; name: string };
export type StartMsg = { type: "start" };
// Drawing is automatic at turn start; a turn ends by playing a card OR passing.
export type PassMsg = { type: "pass" };
export type PlayMsg = {
  type: "play";
  card_id: string;
  placement: Placement;
  chosen_player_id?: string;
};
export type CreateCardMsg = {
  type: "create_card";
  title: string;
  description: string;
};
export type PreviewCardMsg = {
  type: "preview_card";
  title: string;
  description: string;
};
export type EpilogueVoteMsg = {
  type: "epilogue_vote";
  card_id: string;
  keep: boolean;
};

export type ClientMsg =
  | JoinMsg
  | StartMsg
  | PassMsg
  | PlayMsg
  | CreateCardMsg
  | PreviewCardMsg
  | EpilogueVoteMsg;

// ─── server → client ──────────────────────────────────────────────────────

export type CardSnapshot = {
  id: string;
  title: string;
  description: string;
  author_id?: string;
  creator_id?: string;
  program?: string | null;
  snippet?: string | null;
  verdict?: string;
};

export type PlayerSnapshot = {
  id: string;
  name: string;
  score: number;
  hand: string[];
  connected: boolean;
};

export type GameStateSnapshot = {
  room_code: string;
  phase: "lobby" | "setup" | "playing" | "epilogue" | "ended";
  players: PlayerSnapshot[];
  turn_index: number;
  direction: 1 | -1;
  draw_count: number;
  deck: string[];
  discard: string[];
  cards: Record<string, CardSnapshot>;
  house_rules: string[];
  // Populated when phase === "ended": winning player ids (empty = no winner,
  // multiple = tie). Mirrors GameState.winner_ids in the backend.
  winner_ids: string[];
  log: string[];
};

export type StateMsg = { type: "state"; state: GameStateSnapshot };
export type EffectAppliedMsg = { type: "effect_applied"; log_entry: string };
export type CardInterpretedMsg = {
  type: "card_interpreted";
  card_id: string;
  program?: string | null;
  snippet?: string | null;
  verdict: string;
};
export type PreviewResultMsg = {
  type: "preview_result";
  program?: string | null;
  snippet?: string | null;
  verdict: string;
};
export type PromptChoiceMsg = {
  type: "prompt_choice";
  card_id: string;
  prompt: string;
  choices: Array<{ player_id: string; name: string }>;
};
export type EpilogueMsg = { type: "epilogue"; cards: CardSnapshot[] };
export type ErrorMsg = { type: "error"; message: string };
export type BrewingMsg = { type: "brewing"; card_id: string };

export type ServerMsg =
  | StateMsg
  | EffectAppliedMsg
  | CardInterpretedMsg
  | PreviewResultMsg
  | PromptChoiceMsg
  | EpilogueMsg
  | ErrorMsg
  | BrewingMsg;
