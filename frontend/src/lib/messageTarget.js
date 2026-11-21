// A reply that arrives after the user switched agents must not be appended to
// the new agent's conversation. `targetId` is the agent the message was
// produced for; undefined means the caller does not care which agent is active.
export function targetsCurrentAgent(currentId, targetId) {
  return targetId === undefined || targetId === currentId;
}
