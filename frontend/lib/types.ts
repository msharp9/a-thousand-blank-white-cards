// TypeScript mirrors of the backend WS envelopes (tbwc/models/ws_messages.py).

// Room play mode chosen by the host on create. Mirrors the backend's
// POST /rooms body ({"mode": ...}); the backend defaults to "both" when omitted.
export type Mode = "online" | "in_person" | "both";

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
  // Optional/back-compat: the UI no longer collects a zone/target dropdown; the
  // player just picks a card. A target, if the card needs one, is supplied on a
  // follow-up play via chosen_player_id / chosen_card_id in response to a
  // prompt_choice.
  placement?: Placement;
  chosen_player_id?: string;
  chosen_card_id?: string;
  // Author-on-play: when the played card is BLANK, the first play carries the
  // authored title+description. The backend fills in and persists the card
  // before interpreting; a prompt_choice follow-up omits these (the card is
  // already real by then).
  title?: string;
  description?: string;
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
  // True while this is an un-authored blank card (empty title/description). The
  // game seeds blanks into the deck; a blank sits in hand as blank and is
  // authored when played. Cleared once the player fills it in on play.
  blank?: boolean;
};

export type PlayerSnapshot = {
  id: string;
  name: string;
  score: number;
  hand: string[];
  connected: boolean;
  // True for a late joiner seated as a spectator (joined after the game left
  // the lobby). Spectators appear on the table but take no turn and cannot
  // author or play cards. Mirrors Player.spectator on the backend.
  spectator: boolean;
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
// A single selectable option in a prompt_choice. Player-target prompts carry a
// `player_id`; card-target prompts carry a `card_id`. Exactly one is present,
// which tells the UI which field to send back on the follow-up play.
export type PromptChoiceOption = {
  player_id?: string;
  card_id?: string;
  name: string;
};
export type PromptChoiceMsg = {
  type: "prompt_choice";
  card_id: string;
  prompt: string;
  choices: PromptChoiceOption[];
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
