import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function badJsonResponse(status) {
  return {
    ok: false,
    status,
    json: async () => {
      throw new SyntaxError("Unexpected token <");
    },
  };
}

function headerOf(call, name) {
  return (call[1] && call[1].headers && call[1].headers[name]) || undefined;
}

async function load() {
  vi.resetModules();
  const workspace = await import("./lib/workspace.js");
  const api = await import("./api.js");
  return { workspace, api };
}

describe("api client", () => {
  let fetchMock;

  beforeEach(() => {
    localStorage.clear();
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete globalThis.fetch;
  });

  it("attaches the stored token as X-Workspace-Token", async () => {
    const { workspace, api } = await load();
    workspace.setToken("tok-1");
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "a1" }));

    await api.getAgentState("a1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/agents\/a1\/state$/);
    expect(headerOf(fetchMock.mock.calls[0], "X-Workspace-Token")).toBe("tok-1");
  });

  it("creates a workspace on first call, stores the token and uses it", async () => {
    const { workspace, api } = await load();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(201, { token: "new-tok" }))
      .mockResolvedValueOnce(jsonResponse(200, { reply: "hi" }));

    const out = await api.sendMessage("a1", [{ role: "user", content: "yo" }]);

    expect(out).toEqual({ reply: "hi" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/workspaces$/);
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(workspace.getToken()).toBe("new-tok");
    expect(headerOf(fetchMock.mock.calls[1], "X-Workspace-Token")).toBe("new-tok");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      messages: [{ role: "user", content: "yo" }],
    });
  });

  it("creates the workspace exactly once for concurrent first calls", async () => {
    const { api } = await load();
    fetchMock.mockImplementation(async (url) => {
      if (String(url).endsWith("/workspaces")) {
        await new Promise((r) => setTimeout(r, 10));
        return jsonResponse(201, { token: "one-tok" });
      }
      return jsonResponse(200, {});
    });

    await Promise.all([api.getAgentState("a1"), api.getAgentState("a2")]);

    const creates = fetchMock.mock.calls.filter(([u]) =>
      String(u).endsWith("/workspaces"),
    );
    expect(creates).toHaveLength(1);
    const others = fetchMock.mock.calls.filter(
      ([u]) => !String(u).endsWith("/workspaces"),
    );
    expect(others).toHaveLength(2);
    for (const call of others) {
      expect(headerOf(call, "X-Workspace-Token")).toBe("one-tok");
    }
  });

  it("creates the workspace once across sequential calls when setItem throws", async () => {
    const { api } = await load();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    fetchMock.mockImplementation(async (url) =>
      String(url).endsWith("/workspaces")
        ? jsonResponse(201, { token: "mem-tok" })
        : jsonResponse(200, {}),
    );

    await api.getAgentState("a1");
    await api.getAgentState("a1");

    const creates = fetchMock.mock.calls.filter(([u]) =>
      String(u).endsWith("/workspaces"),
    );
    expect(creates).toHaveLength(1);
    const others = fetchMock.mock.calls.filter(
      ([u]) => !String(u).endsWith("/workspaces"),
    );
    expect(headerOf(others[1], "X-Workspace-Token")).toBe("mem-tok");
  });

  it("on 401 clears token and agent id and throws ApiError with status 401", async () => {
    const { workspace, api } = await load();
    workspace.setToken("stale");
    workspace.setAgentId("agent-x");
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "Invalid token" }));

    const err = await api.getAgentState("agent-x").catch((e) => e);

    expect(err).toBeInstanceOf(api.ApiError);
    expect(err).toBeInstanceOf(Error);
    expect(err.status).toBe(401);
    expect(err.message).toBe("Invalid token");
    expect(workspace.getToken()).toBeNull();
    expect(workspace.getAgentId()).toBeNull();
  });

  it("prefers the string detail as the error message", async () => {
    const { workspace, api } = await load();
    workspace.setToken("t");
    fetchMock.mockResolvedValueOnce(jsonResponse(404, { detail: "Agent not found" }));

    const err = await api.getAgentState("nope").catch((e) => e);

    expect(err).toBeInstanceOf(api.ApiError);
    expect(err.status).toBe(404);
    expect(err.message).toBe("Agent not found");
  });

  it("joins msg fields when detail is a validation array", async () => {
    const { workspace, api } = await load();
    workspace.setToken("t");
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, {
        detail: [
          { loc: ["body", "name"], msg: "Field required", type: "missing" },
          { loc: ["body", "system_prompt"], msg: "String too short", type: "x" },
        ],
      }),
    );

    const err = await api.createAgent({ name: "" }).catch((e) => e);

    expect(err.status).toBe(422);
    expect(err.message).toBe("Field required; String too short");
  });

  it("falls back to 'Request failed: <status>' for a non-JSON error body", async () => {
    const { workspace, api } = await load();
    workspace.setToken("t");
    fetchMock.mockResolvedValueOnce(badJsonResponse(502));

    const err = await api.getAgentState("a1").catch((e) => e);

    expect(err).toBeInstanceOf(api.ApiError);
    expect(err.status).toBe(502);
    expect(err.message).toBe("Request failed: 502");
  });

  it("listPresets sends no token and does not create a workspace", async () => {
    const { workspace, api } = await load();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, [{ id: "p1" }]));

    const out = await api.listPresets();

    expect(out).toEqual([{ id: "p1" }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/presets$/);
    expect(headerOf(fetchMock.mock.calls[0], "X-Workspace-Token")).toBeUndefined();
    expect(workspace.getToken()).toBeNull();
  });

  it("deleteWorkspace clears local state after a 204", async () => {
    const { workspace, api } = await load();
    workspace.setToken("t");
    workspace.setAgentId("a");
    fetchMock.mockResolvedValueOnce({ ok: true, status: 204, json: async () => ({}) });

    await api.deleteWorkspace();

    expect(fetchMock.mock.calls[0][0]).toMatch(/\/workspaces\/me$/);
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    expect(workspace.getToken()).toBeNull();
    expect(workspace.getAgentId()).toBeNull();
  });

  it("deleteWorkspace with no stored token makes no request", async () => {
    const { workspace, api } = await load();
    workspace.setAgentId("orphan");

    await api.deleteWorkspace();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(workspace.getToken()).toBeNull();
    expect(workspace.getAgentId()).toBeNull();
  });

  it("deleteWorkspace treats a 401 as success and clears local state", async () => {
    const { workspace, api } = await load();
    workspace.setToken("stale");
    workspace.setAgentId("a");
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "Workspace not found or expired." }));

    await expect(api.deleteWorkspace()).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(workspace.getToken()).toBeNull();
    expect(workspace.getAgentId()).toBeNull();
  });

  it("deleteWorkspace still rejects on other failures and keeps local state", async () => {
    const { workspace, api } = await load();
    workspace.setToken("t");
    fetchMock.mockResolvedValueOnce(jsonResponse(500, { detail: "boom" }));

    await expect(api.deleteWorkspace()).rejects.toThrow("boom");
    expect(workspace.getToken()).toBe("t");
  });

  it("builds the right requests for the other endpoints", async () => {
    const { workspace, api } = await load();
    workspace.setToken("t");
    fetchMock.mockImplementation(async () => jsonResponse(200, {}));

    await api.createAgent({ preset_id: "p" });
    await api.saveRun("a1", [{ role: "user", content: "x" }]);
    await api.checkPattern("a1");
    await api.discoverCriterion("a1");
    await api.addCriterion("a1", { name: "n" });
    await api.updateCriterion("c1", { name: "m" });

    const calls = fetchMock.mock.calls.map(([u, o]) => [
      o.method,
      String(u).replace(/^.*?(?=\/(agents|criteria))/, ""),
    ]);
    expect(calls).toEqual([
      ["POST", "/agents"],
      ["POST", "/agents/a1/runs"],
      ["POST", "/agents/a1/check-pattern"],
      ["POST", "/agents/a1/criteria/discover"],
      ["POST", "/agents/a1/criteria"],
      ["PATCH", "/criteria/c1"],
    ]);
  });
});

describe("tests api", () => {
  let fetchMock;

  beforeEach(() => {
    localStorage.clear();
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete globalThis.fetch;
  });

  it("calls the right method, path and body, with the token header", async () => {
    const { workspace, api } = await load();
    workspace.setToken("tok");
    fetchMock.mockImplementation(async () => jsonResponse(200, {}));

    await api.getTests("a1");
    await api.replayTest("t1");
    await api.runAllTests("a1");
    await api.buildTests("c1");
    await api.getJob("j1");
    await api.updatePrompt("a1", "You are Alex.");
    await api.updatePrompt("a1", "You are Alex, careful.", "pv1");

    const calls = fetchMock.mock.calls.map(([u, o]) => [
      o.method,
      String(u).replace(/^.*?(?=\/(agents|criteria|tests|jobs))/, ""),
      o.body === undefined ? undefined : JSON.parse(o.body),
    ]);
    expect(calls).toEqual([
      ["GET", "/agents/a1/tests", undefined],
      ["POST", "/tests/t1/replay", undefined],
      ["POST", "/agents/a1/tests/run-all", undefined],
      ["POST", "/criteria/c1/build-tests", undefined],
      ["GET", "/jobs/j1", undefined],
      ["PUT", "/agents/a1/prompt", { text: "You are Alex." }],
      ["PUT", "/agents/a1/prompt", { text: "You are Alex, careful.", base_version_id: "pv1" }],
    ]);
    for (const call of fetchMock.mock.calls) {
      expect(headerOf(call, "X-Workspace-Token")).toBe("tok");
    }
  });

  it("returns the parsed body", async () => {
    const { workspace, api } = await load();
    workspace.setToken("tok");
    fetchMock.mockResolvedValueOnce(jsonResponse(202, { job_id: "j9" }));

    await expect(api.replayTest("t1")).resolves.toEqual({ job_id: "j9" });
  });
});

describe("fix-loop api", () => {
  let fetchMock;

  beforeEach(() => {
    localStorage.clear();
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete globalThis.fetch;
  });

  it("calls the right method, path and body, with the token header", async () => {
    const { workspace, api } = await load();
    workspace.setToken("tok");
    fetchMock.mockImplementation(async () => jsonResponse(200, {}));

    await api.generateHeldout("c1");
    await api.suggestFix("t1");
    await api.getVersions("a1");
    await api.getVersion("v1");
    await api.approveVersion("v1");
    await api.rejectVersion("v1");
    await api.restoreVersion("v1");
    await api.getReport("v1");
    await api.getReport("v1", { view: true });

    const calls = fetchMock.mock.calls.map(([u, o]) => [
      o.method,
      String(u).replace(/^.*?(?=\/(agents|criteria|tests|prompt-versions))/, ""),
      o.body === undefined ? undefined : JSON.parse(o.body),
    ]);
    expect(calls).toEqual([
      ["POST", "/criteria/c1/generate-heldout", undefined],
      ["POST", "/tests/t1/suggest-fix", undefined],
      ["GET", "/agents/a1/prompt-versions", undefined],
      ["GET", "/prompt-versions/v1", undefined],
      ["POST", "/prompt-versions/v1/approve", undefined],
      ["POST", "/prompt-versions/v1/reject", undefined],
      ["POST", "/prompt-versions/v1/restore", undefined],
      ["GET", "/prompt-versions/v1/report", undefined],
      ["GET", "/prompt-versions/v1/report?view=1", undefined],
    ]);
    for (const call of fetchMock.mock.calls) {
      expect(headerOf(call, "X-Workspace-Token")).toBe("tok");
    }
  });

  it("returns the parsed body and keeps the server's message on a 409", async () => {
    const { workspace, api } = await load();
    workspace.setToken("tok");
    fetchMock.mockResolvedValueOnce(jsonResponse(202, { job_id: "j9" }));
    await expect(api.suggestFix("t1")).resolves.toEqual({ job_id: "j9" });

    fetchMock.mockResolvedValueOnce(jsonResponse(409, { detail: "This fix can't be approved in its current state." }));
    const err = await api.approveVersion("v1").catch((e) => e);
    expect(err).toBeInstanceOf(api.ApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe("This fix can't be approved in its current state.");
  });
});

