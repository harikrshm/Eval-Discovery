import { clearWorkspace, getToken, setToken } from "./lib/workspace.js";

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function errorFromResponse(res) {
  let message = `Request failed: ${res.status}`;
  try {
    const body = await res.json();
    const detail = body && body.detail;
    if (typeof detail === "string" && detail) {
      message = detail;
    } else if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (d && typeof d.msg === "string" ? d.msg : null))
        .filter(Boolean);
      if (msgs.length) message = msgs.join("; ");
    }
  } catch {
    // Non-JSON body: keep the status fallback.
  }
  return new ApiError(message, res.status);
}

// Shared in-flight promise so concurrent first calls create one workspace.
let workspacePromise = null;

function ensureToken() {
  const existing = getToken();
  if (existing) return Promise.resolve(existing);
  if (!workspacePromise) {
    workspacePromise = (async () => {
      const res = await fetch(`${BASE_URL}/workspaces`, { method: "POST" });
      if (!res.ok) throw await errorFromResponse(res);
      const { token } = await res.json();
      setToken(token);
      return token;
    })().finally(() => {
      workspacePromise = null;
    });
  }
  return workspacePromise;
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) headers["X-Workspace-Token"] = await ensureToken();

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    if (res.status === 401) clearWorkspace();
    throw await errorFromResponse(res);
  }
  if (res.status === 204) return null;
  return res.json();
}

export function listPresets() {
  return request("/presets", { auth: false });
}

export function createAgent(payload) {
  return request("/agents", { method: "POST", body: payload });
}

export function getAgentState(agentId) {
  return request(`/agents/${agentId}/state`);
}

export function sendMessage(agentId, messages) {
  return request(`/agents/${agentId}/chat`, { method: "POST", body: { messages } });
}

export function saveRun(agentId, messages) {
  return request(`/agents/${agentId}/runs`, { method: "POST", body: { messages } });
}

export function checkPattern(agentId) {
  return request(`/agents/${agentId}/check-pattern`, { method: "POST" });
}

export function discoverCriterion(agentId) {
  return request(`/agents/${agentId}/criteria/discover`, { method: "POST" });
}

export function addCriterion(agentId, fields) {
  return request(`/agents/${agentId}/criteria`, { method: "POST", body: fields });
}

export function updateCriterion(criterionId, fields) {
  return request(`/criteria/${criterionId}`, { method: "PATCH", body: fields });
}

export function getTests(agentId) {
  return request(`/agents/${agentId}/tests`);
}

export function replayTest(testId) {
  return request(`/tests/${testId}/replay`, { method: "POST" });
}

export function runAllTests(agentId) {
  return request(`/agents/${agentId}/tests/run-all`, { method: "POST" });
}

export function buildTests(criterionId) {
  return request(`/criteria/${criterionId}/build-tests`, { method: "POST" });
}

export function generateHeldout(criterionId) {
  return request(`/criteria/${criterionId}/generate-heldout`, { method: "POST" });
}

export function suggestFix(testId) {
  return request(`/tests/${testId}/suggest-fix`, { method: "POST" });
}

export function getVersions(agentId) {
  return request(`/agents/${agentId}/prompt-versions`);
}

export function getVersion(versionId) {
  return request(`/prompt-versions/${versionId}`);
}

export function approveVersion(versionId) {
  return request(`/prompt-versions/${versionId}/approve`, { method: "POST" });
}

export function rejectVersion(versionId) {
  return request(`/prompt-versions/${versionId}/reject`, { method: "POST" });
}

export function restoreVersion(versionId) {
  return request(`/prompt-versions/${versionId}/restore`, { method: "POST" });
}

// The suite as a document for the CI gate (`eval-discovery.suite`).
export function exportSuite(agentId) {
  return request(`/agents/${agentId}/export`);
}

// `view` marks the page's first fetch of a report (the server counts one view per `?view=1`);
// the polls that wait for the written explanation leave it out.
export function getReport(versionId, { view = false } = {}) {
  return request(`/prompt-versions/${versionId}/report${view ? "?view=1" : ""}`);
}

export function getJob(jobId) {
  return request(`/jobs/${jobId}`);
}

// `baseVersionId` is the version the text was written against; the server refuses the save (409)
// when the agent has moved on since. Omitted, the save is unconditional.
export function updatePrompt(agentId, text, baseVersionId) {
  return request(`/agents/${agentId}/prompt`, {
    method: "PUT",
    body: baseVersionId === undefined ? { text } : { text, base_version_id: baseVersionId },
  });
}

// Deletes the stored workspace. With no stored token there is nothing to delete (and
// request() would create a workspace just to delete it), so do nothing. A 401 means the
// workspace is already gone or expired, so its data is unreachable: that counts as success.
export async function deleteWorkspace() {
  if (!getToken()) {
    clearWorkspace();
    return;
  }
  try {
    await request("/workspaces/me", { method: "DELETE" });
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 401)) throw err;
  }
  clearWorkspace();
}
