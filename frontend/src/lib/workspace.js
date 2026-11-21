// Anonymous workspace identity, persisted in localStorage.
//
// localStorage can throw (private windows, blocked site data, quota), so every
// access is wrapped and mirrored in module-level memory so the app keeps
// working for the lifetime of the page.

const TOKEN_KEY = "ed_token";
const AGENT_KEY = "ed_agent_id";

const memory = { [TOKEN_KEY]: null, [AGENT_KEY]: null };

function read(key) {
  let value = null;
  try {
    value = localStorage.getItem(key);
  } catch {
    value = null;
  }
  // Fall back to memory whenever storage yields nothing (it threw, or a prior
  // setItem failed). remove() clears memory, so a stale value cannot survive a clear.
  return value ?? memory[key];
}

function write(key, value) {
  memory[key] = value;
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage unavailable; the in-memory value is the fallback.
  }
}

function remove(key) {
  memory[key] = null;
  try {
    localStorage.removeItem(key);
  } catch {
    // Storage unavailable; memory is already cleared.
  }
}

export function getToken() {
  return read(TOKEN_KEY);
}

export function setToken(token) {
  write(TOKEN_KEY, token);
}

export function getAgentId() {
  return read(AGENT_KEY);
}

export function setAgentId(agentId) {
  write(AGENT_KEY, agentId);
}

export function clearWorkspace() {
  remove(TOKEN_KEY);
  remove(AGENT_KEY);
}
