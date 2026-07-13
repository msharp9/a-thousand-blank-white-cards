// TypeScript mirrors of the backend WS envelopes (models/ws_messages.py).

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
// A turn begins with an explicit draw step; the active player then plays a card
// OR ends their turn. Drawing is no longer automatic — the client sends `draw`
// at turn start (gated by has_drawn), and the Play action is blocked until then.
export type DrawMsg = { type: "draw" };
// A turn ends by playing a card OR ending the turn. `pass` and `end_turn` are
// aliases; the backend only lets the active player end without playing when they
// hold no playable card (can_pass).
export type PassMsg = { type: "pass" };
export type EndTurnMsg = { type: "end_turn" };
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
  // Optional card art authored alongside a blank: a PNG data-URL
  // ("data:image/png;base64,…", ≤131072 chars — the server verifies both).
  art?: string;
  // Play this card into the open reaction window (canonical trigger
  // "on_reaction"). Sent by non-active players while pending_play is open; a
  // prompt_choice follow-up for a reaction must re-send this flag.
  as_reaction?: boolean;
};
// Decline to react to the pending play; when every eligible player passes the
// window closes early. window_id is a stale-window guard.
export type PassReactionMsg = { type: "pass_reaction"; window_id?: string };
export type CreateCardMsg = {
  type: "create_card";
  title: string;
  description: string;
  // Optional card art: same PNG data-URL contract as PlayMsg.art.
  art?: string;
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
// A player is done voting; any card they never voted on abstains. This is
// what makes voting skippable instead of requiring full coverage.
export type EpilogueDoneMsg = { type: "epilogue_done" };
// Host-only: finalize the epilogue immediately, regardless of who's done.
export type EpilogueFinalizeMsg = { type: "epilogue_finalize" };
// Host-only: advance from the post-game results screen into the epilogue
// vote. Only valid while phase === "results".
export type EpilogueStartMsg = { type: "epilogue_start" };

export type ClientMsg =
  | JoinMsg
  | StartMsg
  | DrawMsg
  | PassMsg
  | EndTurnMsg
  | PlayMsg
  | PassReactionMsg
  | CreateCardMsg
  | PreviewCardMsg
  | EpilogueStartMsg
  | EpilogueVoteMsg
  | EpilogueDoneMsg
  | EpilogueFinalizeMsg;

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
  // True when the backend has rendered artwork for this card, servable from
  // GET /rooms/{code}/cards/{id}/art (see lib/art.ts).
  has_art?: boolean;
  // Description of the card's art (null/absent when artless). First-class so
  // effects can key off what a card depicts.
  alt_text?: string | null;
  // Structured canonical annotation (schema v2). Typed loosely — the client
  // only reads trigger to spot reaction cards; the data already rides the
  // snapshot.
  canonical?: {
    trigger?: string | null;
    placement?: string;
    venue?: string;
  } | null;
};

export type PlayerSnapshot = {
  id: string;
  name: string;
  score: number;
  hand: string[];
  // Cards this player has played in front of them (visible to everyone on the
  // table). Resolve ids against GameStateSnapshot.cards to render them.
  in_play: string[];
  connected: boolean;
};

// A late joiner who watches but never plays (joined after the game left the
// lobby). Lives in GameStateSnapshot.spectators — a separate collection from
// players — rather than as a flagged PlayerSnapshot. Mirrors
// models.game_state.Spectator on the backend.
export type SpectatorSnapshot = {
  id: string;
  name: string;
};

// One card's epilogue vote outcome (id+title only — enough to render a list).
// Mirrors models.game_state.EpilogueCardOutcome.
export type EpilogueCardOutcome = { id: string; title: string };
// Mirrors models.game_state.EpilogueResultSummary. Rides GameStateSnapshot so
// it survives a reconnect after the vote finalizes.
export type EpilogueResultSummary = {
  kept: EpilogueCardOutcome[];
  destroyed: EpilogueCardOutcome[];
};

export type GameStateSnapshot = {
  room_code: string;
  phase: "lobby" | "setup" | "playing" | "results" | "epilogue" | "ended";
  players: PlayerSnapshot[];
  spectators: SpectatorSnapshot[];
  turn_index: number;
  direction: 1 | -1;
  draw_count: number;
  deck: string[];
  discard: string[];
  cards: Record<string, CardSnapshot>;
  house_rules: string[];
  // Whether the active player has taken their draw step this turn. The Draw
  // button shows while false; the Play action is gated until true.
  has_drawn: boolean;
  // Whether the active player may end their turn without playing. True only when
  // they hold NO playable card (e.g. no blank to author), so the Pass/End turn
  // button is hidden whenever they could still play.
  can_pass: boolean;
  // During setup: {player_id: number of cards authored so far}.
  setup_progress: Record<string, number>;
  // How many cards each player must author during setup (currently 5).
  cards_to_author: number;
  // Winning player ids (empty = no winner, multiple = tie). Set when the deck is
  // exhausted and the game resolves scoring — i.e. populated from the "epilogue"
  // phase onward, not only at "ended". Mirrors GameState.winner_ids.
  winner_ids: string[];
  // Populated once the epilogue vote finalizes (phase === "ended"); null
  // before then, including during the pre-vote "results" phase.
  epilogue_result: EpilogueResultSummary | null;
  // The play currently suspended behind an open reaction window (null when no
  // window is open). Reconnect-safe source of truth; the reaction_window push
  // is just the immediacy signal. Clients compute their own eligibility from
  // their hand's canonical.trigger === "on_reaction".
  pending_play: PendingPlaySnapshot | null;
  log: string[];
};

export type PendingPlaySnapshot = {
  window_id: string;
  card_id: string;
  actor_id: string;
  deadline_epoch_ms: number;
};

export type StateMsg = { type: "state"; state: GameStateSnapshot };
export type EffectAppliedMsg = { type: "effect_applied"; log_entry: string };
export type CardInterpretedMsg = {
  type: "card_interpreted";
  card_id: string;
  program?: string | null;
  snippet?: string | null;
  verdict: string;
  // A short in-character quip from the AI arbiter about the interpreted card.
  // Persisted separately: the backend also appends it to state.log with a "🤖 "
  // prefix and broadcasts it via effect_applied, so the frontend renders it from
  // the log (see EffectLog) rather than from this transient field. Optional to
  // stay compatible with older servers that predate C10.
  comment?: string;
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
// A play opened a reaction window: the pending card is in the state snapshot
// (pending_play); this push carries the deadline for the countdown.
export type ReactionWindowMsg = {
  type: "reaction_window";
  window_id: string;
  card_id: string;
  actor_id: string;
  deadline_epoch_ms: number;
};
// The window closed. "resolved" = timeout or all passed (the original play
// resolved normally); the other outcomes name the reactor and their card.
export type ReactionResultMsg = {
  type: "reaction_result";
  window_id: string;
  outcome: "resolved" | "countered" | "stolen" | "redirected";
  reactor_id?: string | null;
  reaction_card_id?: string | null;
};

export type ServerMsg =
  | StateMsg
  | EffectAppliedMsg
  | CardInterpretedMsg
  | PreviewResultMsg
  | PromptChoiceMsg
  | EpilogueMsg
  | ErrorMsg
  | BrewingMsg
  | ReactionWindowMsg
  | ReactionResultMsg;
