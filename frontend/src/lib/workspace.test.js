import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

async function load() {
  vi.resetModules();
  return import("./workspace.js");
}

describe("workspace storage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns null when nothing is stored", async () => {
    const ws = await load();
    expect(ws.getToken()).toBeNull();
    expect(ws.getAgentId()).toBeNull();
  });

  it("persists token and agent id under the ed_ keys", async () => {
    const ws = await load();
    ws.setToken("tok");
    ws.setAgentId("agent-1");
    expect(localStorage.getItem("ed_token")).toBe("tok");
    expect(localStorage.getItem("ed_agent_id")).toBe("agent-1");
    expect(ws.getToken()).toBe("tok");
    expect(ws.getAgentId()).toBe("agent-1");
  });

  it("reads values already in localStorage on a fresh module load", async () => {
    localStorage.setItem("ed_token", "existing");
    const ws = await load();
    expect(ws.getToken()).toBe("existing");
  });

  it("clearWorkspace removes both values from storage and memory", async () => {
    const ws = await load();
    ws.setToken("tok");
    ws.setAgentId("agent-1");
    ws.clearWorkspace();
    expect(localStorage.getItem("ed_token")).toBeNull();
    expect(localStorage.getItem("ed_agent_id")).toBeNull();
    expect(ws.getToken()).toBeNull();
    expect(ws.getAgentId()).toBeNull();
  });

  it("round-trips token and agent id when localStorage throws", async () => {
    const ws = await load();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    ws.setToken("mem-token");
    ws.setAgentId("mem-agent");
    expect(ws.getToken()).toBe("mem-token");
    expect(ws.getAgentId()).toBe("mem-agent");
    ws.clearWorkspace();
    expect(ws.getToken()).toBeNull();
    expect(ws.getAgentId()).toBeNull();
  });
});
