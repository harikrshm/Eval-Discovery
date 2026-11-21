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
    updatePrompt: vi.fn(),
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

function mount(initialAgentId) {
  const holder = { current: null };
  const record = (value) => {
    holder.current = value;
  };
  function Probe({ agentId, onValue }) {
    onValue(useSession(agentId));
    return null;
  }
  const container = document.createElement("div");
  const root = createRoot(container);
  const render = async (agentId) => {
    await act(async () => {
      root.render(createElement(Probe, { agentId, onValue: record }));
    });
  };
  return {
    holder,
    render: () => render(initialAgentId),
    rerender: render,
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

describe("useSession savePrompt", () => {
  const WITH_VERSION = {
    ...STATE,
    agent: { ...STATE.agent, system_prompt: "You are Alex.", prompt_version_id: "pv1", prompt_version_number: 1 },
  };

  beforeEach(() => {
    sessionStorage.clear();
    vi.clearAllMocks();
    api.getAgentState.mockResolvedValue(WITH_VERSION);
  });

  it("updates the agent's prompt and version without reloading the draft", async () => {
    api.updatePrompt.mockResolvedValue({
      version_id: "pv2",
      version_number: 2,
      text: "You are Alex, careful.",
      changed: true,
    });
    const view = mount("a1");
    await view.render();
    await act(async () => {
      view.holder.current.addMessage({ role: "user", content: "hi" }, "a1");
    });

    let returned = "unset";
    await act(async () => {
      returned = await view.holder.current.savePrompt("You are Alex, careful.");
    });

    expect(api.updatePrompt).toHaveBeenCalledWith("a1", "You are Alex, careful.");
    expect(returned).toBeUndefined();
    const { agent } = view.holder.current;
    expect(agent.system_prompt).toBe("You are Alex, careful.");
    expect(agent.prompt_version_id).toBe("pv2");
    expect(agent.prompt_version_number).toBe(2);
    expect(agent.name).toBe("Alex");
    expect(api.getAgentState).toHaveBeenCalledTimes(1);
    expect(view.holder.current.currentTranscript).toHaveLength(1);
    await view.unmount();
  });

  it("rejects with the server's error and leaves the agent unchanged", async () => {
    api.updatePrompt.mockRejectedValue(new api.ApiError("Prompt version limit reached for this agent.", 429));
    const view = mount("a1");
    await view.render();

    await act(async () => {
      await expect(view.holder.current.savePrompt("Other")).rejects.toThrow(
        "Prompt version limit reached for this agent."
      );
    });
    expect(view.holder.current.agent.prompt_version_number).toBe(1);
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

describe("useSession discover", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.clearAllMocks();
    api.getAgentState.mockResolvedValue({ ...STATE, pattern_summary: "asks about price" });
  });

  it("keeps the build job id out of the stored criterion, so Export all does not include it", async () => {
    api.discoverCriterion.mockResolvedValue({
      id: "c1",
      name: "Answers the pricing question",
      definition: "d",
      good_example: "g",
      bad_example: "b",
      why_discovered: "w",
      source: "discovered",
      job_id: "job-9",
    });
    const view = mount("a1");
    await view.render();

    let returned;
    await act(async () => {
      returned = await view.holder.current.discover();
    });

    const [stored] = view.holder.current.evalCriteria;
    expect(stored.id).toBe("c1");
    expect("job_id" in stored).toBe(false);
    expect("job_id" in returned).toBe(false);
    // Export all writes JSON.stringify(evalCriteria, null, 2).
    expect(JSON.stringify(view.holder.current.evalCriteria, null, 2)).not.toContain("job_id");
    await view.unmount();
  });
});

describe("useSession refreshAgent", () => {
  const WITH_VERSION = {
    ...STATE,
    criteria: [{ id: "c1", name: "N", definition: "d", good_example: "g", bad_example: "b" }],
    pattern_summary: "asks about price",
    agent: { ...STATE.agent, system_prompt: "You are Alex.", prompt_version_id: "pv1", prompt_version_number: 1 },
  };
  const PROMOTED = {
    ...WITH_VERSION,
    criteria: [], // must be ignored: refreshAgent never touches criteria
    pattern_summary: null,
    agent: {
      ...WITH_VERSION.agent,
      name: "Renamed",
      system_prompt: "You are Alex, careful.",
      prompt_version_id: "pv2",
      prompt_version_number: 2,
    },
  };

  beforeEach(() => {
    sessionStorage.clear();
    vi.clearAllMocks();
    api.getAgentState.mockResolvedValue(WITH_VERSION);
  });

  it("updates only the prompt fields and leaves the draft, criteria and pattern state alone", async () => {
    const view = mount("a1");
    await view.render();
    await act(async () => {
      view.holder.current.addMessage({ role: "user", content: "hi" }, "a1");
    });
    expect(view.holder.current.patternState).toBe("pattern_found");
    api.getAgentState.mockResolvedValue(PROMOTED);

    let ok;
    await act(async () => {
      ok = await view.holder.current.refreshAgent();
    });

    expect(ok).toBe(true);
    expect(api.getAgentState).toHaveBeenLastCalledWith("a1");
    const { agent } = view.holder.current;
    expect(agent.system_prompt).toBe("You are Alex, careful.");
    expect(agent.prompt_version_id).toBe("pv2");
    expect(agent.prompt_version_number).toBe(2);
    expect(agent.name).toBe("Alex");
    expect(view.holder.current.currentTranscript).toHaveLength(1);
    expect(view.holder.current.evalCriteria).toHaveLength(1);
    expect(view.holder.current.patternState).toBe("pattern_found");
    expect(view.holder.current.patternSummary).toBe("asks about price");
    expect(view.holder.current.loading).toBe(false);
    await view.unmount();
  });

  it("changes nothing and does not throw when the reload fails", async () => {
    const view = mount("a1");
    await view.render();
    api.getAgentState.mockRejectedValue(new TypeError("Failed to fetch"));

    let ok;
    await act(async () => {
      ok = await view.holder.current.refreshAgent();
    });

    expect(ok).toBe(false);
    expect(view.holder.current.agent.prompt_version_number).toBe(1);
    expect(view.holder.current.loadError).toBeNull();
    await view.unmount();
  });

  it("ignores a reply that lands after switching agents", async () => {
    const view = mount("a1");
    await view.render();
    let resolveLate;
    api.getAgentState.mockReturnValueOnce(new Promise((resolve) => (resolveLate = resolve)));
    let call;
    await act(async () => {
      call = view.holder.current.refreshAgent();
    });

    api.getAgentState.mockResolvedValue({ ...STATE, agent: { ...STATE.agent, id: "a2", name: "Bea" } });
    await view.rerender("a2");
    let ok;
    await act(async () => {
      resolveLate(PROMOTED);
      ok = await call;
    });

    expect(ok).toBe(false);
    expect(view.holder.current.agent.id).toBe("a2");
    expect(view.holder.current.agent.prompt_version_number).toBeUndefined();
    expect(view.holder.current.agent.system_prompt).toBeUndefined();
    await view.unmount();
  });

  it("updates nothing after unmount", async () => {
    const view = mount("a1");
    await view.render();
    let resolveLate;
    api.getAgentState.mockReturnValueOnce(new Promise((resolve) => (resolveLate = resolve)));
    let call;
    await act(async () => {
      call = view.holder.current.refreshAgent();
    });
    const before = view.holder.current.agent;

    await view.unmount();
    await act(async () => {
      resolveLate(PROMOTED);
      await call;
    });

    expect(view.holder.current.agent).toBe(before);
  });
});
