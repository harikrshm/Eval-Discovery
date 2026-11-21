import { ApiError } from "../api";

// Mirrors backend/api/limits.py. The server is the authority; these let the UI
// stop before a request that would be rejected.
export const MAX_USER_MESSAGE_CHARS = 2000;
export const MAX_DRAFT_MESSAGES = 40;
export const MAX_REQUEST_CHARS = 120000;

// Why `nextText` cannot be added to the draft, or null when it fits.
export function draftLimitReason(draft, nextText = "") {
  if (draft.length >= MAX_DRAFT_MESSAGES) return "messages";
  const total = draft.reduce((sum, m) => sum + m.content.length, 0);
  if (total + nextText.length > MAX_REQUEST_CHARS) return "size";
  return null;
}

export function draftLimitHint(reason) {
  if (reason === "messages") {
    return `This run has reached the ${MAX_DRAFT_MESSAGES}-message limit. End the run to save it, or discard it and start a new one.`;
  }
  if (reason === "size") {
    return "This run is too long to continue. End the run to save it, or discard it and start a new one.";
  }
  return null;
}

function isClientError(err) {
  return err instanceof ApiError && err.status >= 400 && err.status < 500 && Boolean(err.message);
}

// A 4xx carries a message the user can act on (rate limit, too long); anything else is a connection problem.
export function chatErrorMessage(err, agentName) {
  if (isClientError(err)) return err.message;
  return `${agentName} didn't respond. Check your connection, then try again.`;
}

export function runSaveErrorMessage(err) {
  if (isClientError(err)) return err.message;
  return "Couldn't save the run. Check your connection, then try again.";
}

// The 429 raised when a workspace already has its maximum number of agents.
export function isAgentLimitError(err) {
  return err instanceof ApiError && err.status === 429 && /agent limit/i.test(err.message);
}
