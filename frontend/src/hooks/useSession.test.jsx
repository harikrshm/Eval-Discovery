import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    getAgentState: vi.fn(),
    saveRun: vi.fn(),
    checkPattern: vi.fn(),
    discoverCriterion: vi.fn(),
    addCriterion: vi.fn(),
    updateCriterion: vi.fn(),
  };
});

import * as api from "../api";
import { useSession } from "./useSession";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const STATE = {
  agent: { id: "a1", name: "Alex", user_role_label: "Prospect", starter_messages: [] },
  runs: [],
  criteria: [],
  pattern_summary: null,
  run_count: 0,
  last_checked_at_run: 0,
};

function mount(agentId) {
  const holder = { current: null };
  const record = (value) => {
    holder.current = value;
  };
  function Probe({ onValue }) {
    onValue(useSession(agentId));
    return null;
  }
  const container = document.createElement("div");
  const root = createRoot(container);
  return {
    holder,
    async render() {
      await act(async () => {
        root.render(createElement(Probe, { onValue: record }));
      });
    },
    unmount: () => act(() => root.unmount()),
  };
}

describe("useSession run save failure", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.clearAllMocks();
    api.getAgentState.mockResolvedValue(STATE);
  });

  afterEach(() => {
    sessionStorage.clear();
  });

  it("flags only runSaveFailed and leaves the pattern state alone", async () => {
    api.saveRun.mockRejectedValue(new api.ApiError("Run limit reached for this workspace.", 429));
    const view = mount("a1");
    await view.render();
    expect(view.holder.current.patternState).toBe("idle");

    await act(async () => {
      view.holder.current.addMessage({ role: "user", content: "hi" }, "a1");
      view.holder.current.addMessage({ role: "assistant", content: "yo" }, "a1");
      view.holder.current.addMessage({ role: "user", content: "price?" }, "a1");
    });
    await act(async () => {
      await view.holder.current.saveRun();
    });

    expect(view.holder.current.runSaveFailed).toBe(true);
    expect(view.holder.current.runSaveMessage).toBe("Run limit reached for this workspace.");
    expect(view.holder.current.patternState).toBe("idle");
    expect(view.holder.current.currentTranscript).toHaveLength(3); // draft kept for a retry
    await view.unmount();
  });

  it("keeps an existing pattern_found state when a later save fails", async () => {
    api.getAgentState.mockResolvedValue({ ...STATE, pattern_summary: "asks about price" });
    api.saveRun.mockRejectedValue(new TypeError("Failed to fetch"));
    const view = mount("a1");
    await view.render();
    expect(view.holder.current.patternState).toBe("pattern_found");

    await act(async () => {
      await view.holder.current.saveRun();
    });

    expect(view.holder.current.runSaveFailed).toBe(true);
    expect(view.holder.current.patternState).toBe("pattern_found");
    await view.unmount();
  });
});

describe("useSession discardDraft", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.clearAllMocks();
    api.getAgentState.mockResolvedValue(STATE);
  });

  it("clears the draft and its stored copy without saving", async () => {
    const view = mount("a1");
    await view.render();
    await act(async () => {
      view.holder.current.addMessage({ role: "user", content: "hi" }, "a1");
    });
    expect(sessionStorage.getItem("current_transcript:a1")).not.toBeNull();

    await act(async () => {
      view.holder.current.discardDraft();
    });

    expect(view.holder.current.currentTranscript).toEqual([]);
    expect(sessionStorage.getItem("current_transcript:a1")).toBeNull();
    expect(api.saveRun).not.toHaveBeenCalled();
    await view.unmount();
  });
});

describe("useSession criteria errors", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getAgentState.mockResolvedValue({
      ...STATE,
      criteria: [{ id: "c1", name: "N", definition: "d", good_example: "g", bad_example: "b" }],
    });
  });

  it("words a failed add differently from a failed edit", async () => {
    api.addCriterion.mockRejectedValue(new api.ApiError("Criteria limit reached for this agent.", 429));
    api.updateCriterion.mockRejectedValue(new api.ApiError("nope", 422));
    const view = mount("a1");
    await view.render();

    await act(async () => {
      await view.holder.current.addCriteria({ name: "x", definition: "", good_example: "", bad_example: "" });
    });
    const addMsg = view.holder.current.criteriaSaveError;
    expect(addMsg).toContain("Criteria limit reached for this agent.");
    expect(addMsg).not.toMatch(/last saved value was kept/i);

    await act(async () => {
      await view.holder.current.updateCriteria("c1", { name: "Changed" });
    });
    const updateMsg = view.holder.current.criteriaSaveError;
    expect(updateMsg).toContain("nope");
    expect(updateMsg).toMatch(/last saved value was kept/i);
    await view.unmount();
  });
});
