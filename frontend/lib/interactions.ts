import type {
  InteractionResponseMsg,
  InteractionResponsePayload,
} from "./types";

export function interactionResponseMessage(
  interactionId: string,
  payload: InteractionResponsePayload,
): InteractionResponseMsg {
  return {
    type: "interaction_response",
    schema_version: 1,
    interaction_id: interactionId,
    payload,
  };
}
