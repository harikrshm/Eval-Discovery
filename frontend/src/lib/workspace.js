// Anonymous workspace identity, persisted in localStorage.
//
// localStorage can throw (private windows, blocked site data, quota), so every
// access is wrapped and mirrored in module-level memory so the app keeps
// working for the lifetime of the page.

const TOKEN_KEY = "ed_token";
const AGENT_KEY = "ed_agent_id";

const TOKEN_BACKUP_KEY = "ed_token_backup";
const AGENT_BACKUP_KEY = "ed_agent_id_backup";
const CONNECTED_KEY = "ed_assist_connected";

const memory = {
  [TOKEN_KEY]: null,
  [AGENT_KEY]: null,
  [TOKEN_BACKUP_KEY]: null,
  [AGENT_BACKUP_KEY]: null,
  [CONNECTED_KEY]: null,
};

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

export function clearAgentId() {
  remove(AGENT_KEY);
}

export function clearWorkspace() {
  remove(TOKEN_KEY);
  remove(AGENT_KEY);
}

// ---- Assist console connection ------------------------------------------------------------
//
// Connecting swaps the app's workspace for the demo workspace the pasted token belongs to. The
// demo token is validated first (api.js sends it per call without touching storage); only then does
// commitAssistSwap write everything in one synchronous step: the previous token and agent id under
// their own keys, the demo token, the assistant agent id and the connected marker. A reload or a
// closed tab can therefore never leave the demo token active without the marker, and a backup is
// never overwritten. The token only ever lives in storage and in the request header.

export const ASSIST_SWAP_REFUSED = "Disconnect from the Assist console before connecting again.";

export function isAssistConnected() {
  return read(CONNECTED_KEY) === "1";
}

export function hasAssistBackup() {
  return read(TOKEN_BACKUP_KEY) != null || read(AGENT_BACKUP_KEY) != null;
}

function copyOrRemove(from, to) {
  const value = read(from);
  if (value == null) remove(to);
  else write(to, value);
}

// Backs up the current identity and makes the (already validated) demo token and assistant agent
// the active ones. Refuses, changing nothing, while connected or while a backup exists: a backup
// must never be replaced by the demo token.
export function commitAssistSwap(token, agentId) {
  if (isAssistConnected() || hasAssistBackup()) throw new Error(ASSIST_SWAP_REFUSED);
  copyOrRemove(TOKEN_KEY, TOKEN_BACKUP_KEY);
  copyOrRemove(AGENT_KEY, AGENT_BACKUP_KEY);
  write(TOKEN_KEY, token);
  write(AGENT_KEY, agentId);
  write(CONNECTED_KEY, "1");
}

// Puts the backed-up token and agent id back (or clears them when there were none).
export function restoreAssistBackup() {
  copyOrRemove(TOKEN_BACKUP_KEY, TOKEN_KEY);
  copyOrRemove(AGENT_BACKUP_KEY, AGENT_KEY);
  remove(TOKEN_BACKUP_KEY);
  remove(AGENT_BACKUP_KEY);
  remove(CONNECTED_KEY);
}

// Startup recovery: a backup without the connected marker is an interrupted swap (an older build
// wrote the demo token before validating it). The user's own workspace comes back from the backup.
export function recoverInterruptedSwap() {
  if (!isAssistConnected() && hasAssistBackup()) restoreAssistBackup();
}

// Another tab changed storage: take its values over the in-memory mirror, which would otherwise
// keep answering with what this tab last wrote.
export function applyStorageEvent(event) {
  if (event.key === null) {
    for (const key of Object.keys(memory)) memory[key] = null;
  } else if (event.key in memory) {
    memory[event.key] = event.newValue;
  }
}
