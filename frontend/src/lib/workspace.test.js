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
    it("commits in one step: backs up the identity, then restores it", async () => {
      const ws = await load();
      ws.setToken("mine");
      ws.setAgentId("a1");
      expect(ws.isAssistConnected()).toBe(false);

      ws.commitAssistSwap("demo", "assist-1");
      expect(ws.getToken()).toBe("demo");
      expect(ws.getAgentId()).toBe("assist-1");
      expect(localStorage.getItem("ed_token_backup")).toBe("mine");
      expect(localStorage.getItem("ed_agent_id_backup")).toBe("a1");
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
      ws.commitAssistSwap("demo", "assist-1");
      ws.restoreAssistBackup();
      expect(ws.getToken()).toBeNull();
      expect(ws.getAgentId()).toBeNull();
    });

    it("refuses a second swap and leaves the first backup and the connection untouched", async () => {
      const ws = await load();
      ws.setToken("mine");
      ws.setAgentId("a1");
      ws.commitAssistSwap("demo", "assist-1");
      expect(() => ws.commitAssistSwap("other-demo", "assist-2")).toThrow(ws.ASSIST_SWAP_REFUSED);
      expect(localStorage.getItem("ed_token_backup")).toBe("mine");
      expect(localStorage.getItem("ed_agent_id_backup")).toBe("a1");
      expect(ws.getToken()).toBe("demo");
      expect(ws.getAgentId()).toBe("assist-1");
      ws.restoreAssistBackup();
      expect(ws.getToken()).toBe("mine");
    });

    it("refuses a swap while a backup exists even without the marker", async () => {
      const ws = await load();
      localStorage.setItem("ed_token_backup", "mine");
      localStorage.setItem("ed_token", "demo");
      expect(() => ws.commitAssistSwap("x", "y")).toThrow(ws.ASSIST_SWAP_REFUSED);
      expect(localStorage.getItem("ed_token_backup")).toBe("mine");
      expect(localStorage.getItem("ed_token")).toBe("demo");
    });

    it("recovers an interrupted swap (backup, no marker) from the backup", async () => {
      localStorage.setItem("ed_token", "demo");
      localStorage.setItem("ed_agent_id", "assist-1");
      localStorage.setItem("ed_token_backup", "mine");
      localStorage.setItem("ed_agent_id_backup", "a1");
      const ws = await load();
      ws.recoverInterruptedSwap();
      expect(ws.getToken()).toBe("mine");
      expect(ws.getAgentId()).toBe("a1");
      expect(localStorage.getItem("ed_token_backup")).toBeNull();
      expect(localStorage.getItem("ed_agent_id_backup")).toBeNull();
    });

    it("recovery leaves a connected state and a plain state alone", async () => {
      const ws = await load();
      ws.setToken("mine");
      ws.recoverInterruptedSwap();
      expect(ws.getToken()).toBe("mine");

      ws.commitAssistSwap("demo", "assist-1");
      ws.recoverInterruptedSwap();
      expect(ws.getToken()).toBe("demo");
      expect(ws.isAssistConnected()).toBe(true);
    });

    it("takes over another tab's change of the connected marker", async () => {
      const ws = await load();
      ws.commitAssistSwap("demo", "assist-1");
      expect(ws.isAssistConnected()).toBe(true);
      // Another tab disconnected: storage is cleared there, and the event reaches this tab.
      localStorage.removeItem("ed_assist_connected");
      expect(ws.isAssistConnected()).toBe(true); // still answered from this tab's memory
      ws.applyStorageEvent({ key: "ed_assist_connected", newValue: null });
      expect(ws.isAssistConnected()).toBe(false);
      ws.applyStorageEvent({ key: null });
      localStorage.clear();
      expect(ws.getToken()).toBeNull();
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
      ws.commitAssistSwap("demo", "assist-1");
      expect(ws.isAssistConnected()).toBe(true);
      ws.restoreAssistBackup();
      expect(ws.getToken()).toBe("mine");
    });
  });
});
