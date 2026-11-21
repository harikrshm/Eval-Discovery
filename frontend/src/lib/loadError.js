import { ApiError } from "../api";

// A 404 (agent deleted) or 401 (workspace gone) means the stored agent cannot
// come back, so the user must pick or create another one. Anything else (network
// failure, timeout, 5xx) is transient: the agent still exists and loading can be retried.
export function isAgentGone(err) {
  return err instanceof ApiError && (err.status === 401 || err.status === 404);
}
