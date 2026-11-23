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

  it("round-trips token and agent id when only setItem throws", async () => {
    const ws = await load();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    ws.setToken("mem-token");
    ws.setAgentId("mem-agent");
    expect(ws.getToken()).toBe("mem-token");
    expect(ws.getAgentId()).toBe("mem-agent");
    ws.clearWorkspace();
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

  it("clearAgentId removes only the agent id and keeps the token", async () => {
    const ws = await load();
    ws.setToken("tok");
    ws.setAgentId("agent-1");
    ws.clearAgentId();
    expect(localStorage.getItem("ed_agent_id")).toBeNull();
    expect(localStorage.getItem("ed_token")).toBe("tok");
    expect(ws.getAgentId()).toBeNull();
    expect(ws.getToken()).toBe("tok");
  });

  it("clearAgentId clears the in-memory fallback when localStorage throws", async () => {
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
    ws.clearAgentId();
    expect(ws.getAgentId()).toBeNull();
    expect(ws.getToken()).toBe("mem-token");
  });
  describe("assist connection", () => {
    it("backs up the identity, swaps in the demo token and restores it", async () => {
      const ws = await load();
      ws.setToken("mine");
      ws.setAgentId("a1");

      ws.beginAssistSwap("demo");
      expect(ws.getToken()).toBe("demo");
      expect(ws.getAgentId()).toBeNull();
      expect(localStorage.getItem("ed_token_backup")).toBe("mine");
      expect(localStorage.getItem("ed_agent_id_backup")).toBe("a1");
      expect(ws.isAssistConnected()).toBe(false);

      ws.completeAssistSwap("assist-1");
      expect(ws.getAgentId()).toBe("assist-1");
      expect(ws.isAssistConnected()).toBe(true);

      ws.restoreAssistBackup();
      expect(ws.getToken()).toBe("mine");
      expect(ws.getAgentId()).toBe("a1");
      expect(ws.isAssistConnected()).toBe(false);
      expect(localStorage.getItem("ed_token_backup")).toBeNull();
      expect(localStorage.getItem("ed_agent_id_backup")).toBeNull();
    });

    it("clears the workspace on restore when there was nothing before", async () => {
      const ws = await load();
      ws.beginAssistSwap("demo");
      ws.completeAssistSwap("assist-1");
      ws.restoreAssistBackup();
      expect(ws.getToken()).toBeNull();
      expect(ws.getAgentId()).toBeNull();
    });

    it("keeps the first backup when a swap begins while already connected", async () => {
      const ws = await load();
      ws.setToken("mine");
      ws.beginAssistSwap("demo");
      ws.completeAssistSwap("assist-1");
      ws.beginAssistSwap("other-demo");
      ws.restoreAssistBackup();
      expect(ws.getToken()).toBe("mine");
    });

    it("works from memory when storage is unavailable", async () => {
      const ws = await load();
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("blocked");
      });
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("blocked");
      });
      ws.setToken("mine");
      ws.beginAssistSwap("demo");
      ws.completeAssistSwap("assist-1");
      expect(ws.isAssistConnected()).toBe(true);
      ws.restoreAssistBackup();
      expect(ws.getToken()).toBe("mine");
    });
  });
});
