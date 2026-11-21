import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";

vi.mock("../hooks/useReport", () => ({ useReport: vi.fn() }));

import { useReport } from "../hooks/useReport";
import EvalCriteriaPanel from "./EvalCriteriaPanel";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

let container;
let root;

const CRITERION = {
  id: "c1",
  name: "Stays on topic",
  why_discovered: "Seen in two runs.",
  definition: "d",
  good_example: "g",
  bad_example: "b",
};

function suiteWith({ stale = 0, tests = [{ id: "t1", role: "fix", baseline: null, current: null, active_job_id: null }] } = {}) {
  return {
    prompt_version_id: "pv2",
    prompt_version_number: 2,
    stale_count: stale,
    criteria: [{ criterion_id: "c1", status: "failing", judge_check: null, active_job_id: null, tests }],
  };
}

const lastEditor = { promptText: "You are Alex.", versionNumber: 2, onSave: vi.fn(async () => {}) };

function panelProps(props) {
  return {
    evalCriteria: [CRITERION],
    patternState: "complete",
    discoverPending: false,
    onDismiss: vi.fn(),
    onUpdate: vi.fn(),
    onAdd: vi.fn(),
    onExportAll: vi.fn(),
    saveError: null,
    suite: suiteWith(),
    jobs: {},
    onReplay: vi.fn(),
    onBuildTests: vi.fn(),
    onRunAll: vi.fn(),
    agentName: "Alex",
    userRoleLabel: "Prospect",
    promptEditor: lastEditor,
    ...props,
  };
}

async function render(props) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(EvalCriteriaPanel, panelProps(props)));
  });
}

// Re-renders the mounted panel with the same defaults plus these props (a parent state change).
async function rerender(props) {
  await act(async () => {
    root.render(createElement(EvalCriteriaPanel, panelProps(props)));
  });
}

function buttonByText(text) {
  return [...container.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

beforeEach(() => {
  document.body.innerHTML = "";
  vi.mocked(useReport).mockReset();
  vi.mocked(useReport).mockReturnValue({ report: null, version: null, loading: true, error: null });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("EvalCriteriaPanel prompt editor and tests", () => {
  it("renders the prompt editor with the current version", async () => {
    await render({});
    expect(container.textContent).toContain("Chat uses v2");
  });

  it("renders each criterion's tests inside its card", async () => {
    await render({});
    expect(container.textContent).toContain("Fix test");
    expect(container.textContent).toContain("Failing");
  });
});

describe("EvalCriteriaPanel stale banner", () => {
  it("shows no banner when nothing is stale", async () => {
    await render({ suite: suiteWith({ stale: 0 }) });
    expect(buttonByText("Run all tests")).toBeUndefined();
  });

  it("shows no banner when the suite has no tests, even if the count is non-zero", async () => {
    await render({ suite: suiteWith({ stale: 1, tests: [] }) });
    expect(buttonByText("Run all tests")).toBeUndefined();
  });

  it("shows the stale message and a Run all tests button that calls onRunAll", async () => {
    const onRunAll = vi.fn();
    await render({ suite: suiteWith({ stale: 2 }), onRunAll });
    expect(container.textContent).toContain("2 tests haven't run on prompt v2 yet.");

    await act(async () => {
      buttonByText("Run all tests").click();
    });
    expect(onRunAll).toHaveBeenCalledTimes(1);
  });

  it("uses the singular and the suite's prompt version", async () => {
    await render({ suite: { ...suiteWith({ stale: 1 }), prompt_version_number: 5 } });
    expect(container.textContent).toContain("1 test hasn't run on prompt v5 yet.");
  });

  it("disables Run all tests while any replay job is queued or running", async () => {
    await render({
      suite: suiteWith({ stale: 1 }),
      jobs: { "replay:t1": { status: "queued", error: null, result: null } },
    });
    expect(buttonByText("Run all tests").disabled).toBe(true);
  });

  it("keeps Run all tests enabled when only a build job is active or a replay failed", async () => {
    await render({
      suite: suiteWith({ stale: 1 }),
      jobs: {
        "build:c1": { status: "running", error: null, result: null },
        "replay:t1": { status: "failed", error: "x", result: null },
      },
    });
    expect(buttonByText("Run all tests").disabled).toBe(false);
  });
});

describe("EvalCriteriaPanel before the suite has loaded", () => {
  it("still renders the criteria and the prompt editor", async () => {
    await render({ suite: null });
    expect(container.textContent).toContain("Stays on topic");
    expect(container.textContent).toContain("Chat uses v2");
    expect(buttonByText("Run all tests")).toBeUndefined();
  });
});

describe("EvalCriteriaPanel tests load error", () => {
  const alerts = () => [...container.querySelectorAll('[role="alert"]')].filter((a) => a.textContent.includes("load tests"));

  it("shows an alert with the error and a Retry button when the first load failed", async () => {
    const onRetryTests = vi.fn();
    await render({ suite: null, testsError: "Backend down", onRetryTests });
    const [alert] = alerts();
    expect(alert.textContent).toContain("Couldn't load tests.");
    expect(alert.textContent).toContain("Backend down");

    await act(async () => {
      [...alert.querySelectorAll("button")].find((b) => b.textContent === "Retry").click();
    });
    expect(onRetryTests).toHaveBeenCalledTimes(1);
  });

  it("shows no alert when the suite loaded without error", async () => {
    await render({ testsError: null });
    expect(alerts()).toHaveLength(0);
  });

  it("keeps the stale suite visible alongside the alert when a refresh failed", async () => {
    await render({ suite: suiteWith({ stale: 1 }), testsError: "Failed to fetch" });
    expect(alerts()).toHaveLength(1);
    expect(container.textContent).toContain("Fix test");
    expect(buttonByText("Run all tests")).toBeDefined();
  });

  it("hides the alert while a load is in flight", async () => {
    await render({ suite: null, testsError: "Backend down", testsLoading: true });
    expect(alerts()).toHaveLength(0);
  });
});

describe("EvalCriteriaPanel announcements", () => {
  it("makes the stale banner a status region", async () => {
    await render({ suite: suiteWith({ stale: 2 }) });
    const banner = [...container.querySelectorAll('[role="status"]')].find((el) =>
      el.textContent.includes("haven't run on prompt")
    );
    expect(banner).toBeDefined();
    expect(banner.textContent).toContain("Run all tests");
  });
});

describe("EvalCriteriaPanel change review", () => {
  const CANDIDATE = {
    version_id: "v3",
    number: 3,
    status: "proposed",
    origin: "suggested",
    strength: "full",
    created_at: "2026-11-22T10:00:00",
    progress: null,
    parent_number: 2,
  };

  const REPORT = {
    version_id: "v3",
    number: 3,
    status: "proposed",
    strength: "full",
    why: { criterion_name: "Stays on topic", failing_quote: "q", change_summary: "s", touched_section: "t", expected_effect: "e", risk: "r" },
    outcome: null,
  };

  it("shows no change review without a candidate", async () => {
    await render({ suite: { ...suiteWith(), candidate: null } });
    expect(container.textContent).not.toContain("Suggested fix");
    expect(useReport).not.toHaveBeenCalled();
  });

  it("shows no change review before the suite has loaded", async () => {
    await render({ suite: null });
    expect(container.textContent).not.toContain("Suggested fix");
  });

  it("renders the change review for the candidate, above the criteria", async () => {
    vi.mocked(useReport).mockReturnValue({
      report: REPORT,
      version: { id: "v3", number: 3, text: "new", parent_text: "old" },
      loading: false,
      error: null,
    });
    await render({ suite: { ...suiteWith(), candidate: CANDIDATE } });
    expect(useReport).toHaveBeenCalledWith("v3", { enabled: true });
    const heading = [...container.querySelectorAll("h3")].find((h) => h.textContent === "Suggested fix v3");
    expect(heading).toBeDefined();
    const criterion = [...container.querySelectorAll("button")].find((b) => b.textContent.includes("Stays on topic"));
    expect(heading.compareDocumentPosition(criterion) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const editor = container.querySelector('section[aria-label="Agent prompt"]');
    expect(editor.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  const LOADED = {
    report: REPORT,
    version: { id: "v3", number: 3, text: "new", parent_text: "old" },
    loading: false,
    error: null,
  };

  it("passes the approve and reject handlers through by version id", async () => {
    vi.mocked(useReport).mockReturnValue(LOADED);
    const onApprove = vi.fn();
    const onReject = vi.fn();
    await render({ suite: { ...suiteWith(), candidate: CANDIDATE }, onApprove, onReject });
    await act(async () => {
      buttonByText("Approve and verify").click();
    });
    await act(async () => {
      buttonByText("Reject").click();
    });
    expect(onApprove).toHaveBeenCalledWith("v3");
    expect(onReject).toHaveBeenCalledWith("v3");
  });

  it("disables the actions while the version's action is queued", async () => {
    vi.mocked(useReport).mockReturnValue(LOADED);
    await render({
      suite: { ...suiteWith(), candidate: CANDIDATE },
      jobs: { "version:v3": { status: "queued", error: null } },
    });
    expect(buttonByText("Approve and verify").disabled).toBe(true);
    expect(buttonByText("Reject").disabled).toBe(true);
  });

  it("remounts the review when the candidate changes status so the report is fetched again", async () => {
    await render({ suite: { ...suiteWith(), candidate: CANDIDATE } });
    const before = container.querySelector('[aria-label="Change review"]');
    expect(before).not.toBeNull();
    vi.mocked(useReport).mockClear();
    await rerender({
      suite: { ...suiteWith(), candidate: { ...CANDIDATE, status: "approved_verifying", progress: { done: 0, total: 2 } } },
    });
    expect(useReport).toHaveBeenCalled();
    expect(container.querySelector('[aria-label="Change review"]')).not.toBe(before);
  });

  describe("announcements", () => {
    const region = () => container.querySelector('[role="status"].sr-only');

    it("keeps one panel-level live region that exists before its text changes", async () => {
      vi.mocked(useReport).mockReturnValue(LOADED);
      await render({ suite: { ...suiteWith(), candidate: CANDIDATE } });
      const live = region();
      expect(live).not.toBeNull();
      expect(live.textContent).toBe("Suggested fix v3 is ready to review.");

      await rerender({
        suite: { ...suiteWith(), candidate: { ...CANDIDATE, status: "approved_verifying", progress: { done: 0, total: 2 } } },
      });
      expect(region()).toBe(live);
      expect(live.textContent).toBe("Verifying v3. Chat still uses v2.");

      vi.mocked(useReport).mockReturnValue({
        ...LOADED,
        report: {
          ...REPORT,
          status: "promoted",
          outcome: {
            status: "promoted",
            reason: "ok",
            table: [],
            narrative: { explanation: "It worked.", next_step: "Done.", cited_test_ids: [] },
            explanation_unavailable: false,
          },
        },
      });
      await rerender({ suite: { ...suiteWith(), candidate: { ...CANDIDATE, status: "promoted" } } });
      expect(region()).toBe(live);
      expect(live.textContent).toBe("v3 is now live. The explanation is ready.");
    });

    it("exists even without a candidate and is empty", async () => {
      await render({ suite: { ...suiteWith(), candidate: null } });
      expect(region()).not.toBeNull();
      expect(region().textContent).toBe("");
    });

    it("does not duplicate the announcement in a second live region of the review", async () => {
      vi.mocked(useReport).mockReturnValue(LOADED);
      await render({ suite: { ...suiteWith(), candidate: { ...CANDIDATE, status: "approved_verifying", progress: { done: 1, total: 2 } } } });
      const live = [...container.querySelectorAll('[role="status"]')].filter((el) => el.textContent.includes("Verifying v3"));
      expect(live).toHaveLength(1);
    });

    it("announces a rejection", async () => {
      vi.mocked(useReport).mockReturnValue(LOADED);
      const onReject = vi.fn(async () => {
        await rerender({ suite: { ...suiteWith(), candidate: null }, onReject });
        return true;
      });
      await render({ suite: { ...suiteWith(), candidate: CANDIDATE }, onReject });
      await act(async () => {
        buttonByText("Reject").click();
      });
      expect(region().textContent).toBe("Suggested fix v3 rejected.");
    });
  });

  describe("focus", () => {
    const VERIFYING = { ...CANDIDATE, status: "approved_verifying", progress: { done: 0, total: 2 } };

    beforeEach(() => {
      vi.mocked(useReport).mockReturnValue(LOADED);
    });

    it("moves focus to the new state's heading after the user's Approve settles", async () => {
      const onApprove = vi.fn(async () => {
        await rerender({ suite: { ...suiteWith(), candidate: VERIFYING }, onApprove });
        return true;
      });
      await render({ suite: { ...suiteWith(), candidate: CANDIDATE }, onApprove });
      buttonByText("Approve and verify").focus();
      await act(async () => {
        buttonByText("Approve and verify").click();
      });
      expect(document.activeElement.tagName).toBe("H3");
      expect(document.activeElement.textContent).toBe("What happened with v3");
    });

    it("moves focus even when the suite refresh renders before the handler resolves", async () => {
      let finish;
      const onApprove = vi.fn(() => new Promise((resolve) => (finish = resolve)));
      await render({ suite: { ...suiteWith(), candidate: CANDIDATE }, onApprove });
      await act(async () => {
        buttonByText("Approve and verify").click();
      });
      await rerender({ suite: { ...suiteWith(), candidate: VERIFYING }, onApprove });
      expect(document.activeElement.tagName).not.toBe("H3");
      await act(async () => finish(true));
      expect(document.activeElement.textContent).toBe("What happened with v3");
    });

    it("leaves focus alone when Approve fails", async () => {
      const onApprove = vi.fn(async () => false);
      await render({ suite: { ...suiteWith(), candidate: CANDIDATE }, onApprove });
      const approve = buttonByText("Approve and verify");
      approve.focus();
      await act(async () => {
        approve.click();
      });
      expect(document.activeElement).toBe(approve);
      // A later background change must not steal focus either.
      await rerender({ suite: { ...suiteWith(), candidate: VERIFYING }, onApprove });
      expect(document.activeElement.tagName).not.toBe("H3");
    });

    it("moves focus to the panel heading after Reject removes the review", async () => {
      const onReject = vi.fn(async () => {
        await rerender({ suite: { ...suiteWith(), candidate: null }, onReject });
        return true;
      });
      await render({ suite: { ...suiteWith(), candidate: CANDIDATE }, onReject });
      buttonByText("Reject").focus();
      await act(async () => {
        buttonByText("Reject").click();
      });
      expect(document.activeElement.tagName).toBe("H2");
      expect(document.activeElement.textContent).toBe("Eval criteria");
      expect(document.activeElement.getAttribute("tabindex")).toBe("-1");
    });

    it("does not move focus when the status changes without a user click", async () => {
      await render({ suite: { ...suiteWith(), candidate: CANDIDATE } });
      const edit = buttonByText("Edit prompt");
      edit.focus();
      await rerender({ suite: { ...suiteWith(), candidate: VERIFYING } });
      expect(document.activeElement).toBe(edit);
      await rerender({ suite: { ...suiteWith(), candidate: { ...CANDIDATE, status: "promoted" } } });
      expect(document.activeElement).toBe(edit);
      await rerender({ suite: { ...suiteWith(), candidate: null } });
      expect(document.activeElement).toBe(edit);
    });
  });

  describe("report load failure", () => {
    it("offers Retry that refetches and keeps focus in the review, and keeps Reject reachable", async () => {
      vi.mocked(useReport).mockReturnValue({ report: null, version: null, loading: false, error: "Backend down", gaveUp: false });
      const onReject = vi.fn();
      await render({ suite: { ...suiteWith(), candidate: CANDIDATE }, onReject });
      expect(container.textContent).toContain("Couldn't load the suggested fix.");
      expect(buttonByText("Reject")).toBeDefined();
      const before = container.querySelector('[aria-label="Change review"]');
      vi.mocked(useReport).mockClear();

      buttonByText("Retry").focus();
      await act(async () => {
        buttonByText("Retry").click();
      });
      expect(useReport).toHaveBeenCalled();
      expect(container.querySelector('[aria-label="Change review"]')).not.toBe(before);
      expect(document.activeElement.tagName).toBe("H3");
    });
  });
});

describe("EvalCriteriaPanel readiness chip", () => {
  const chip = () => container.querySelector('[data-testid="readiness-chip"]');

  it("shows the readiness of the suite as plain text in the header (the app header chip is the live one)", async () => {
    await render({});
    expect(chip().textContent).toBe("0 of 1 criteria passing");
    expect(chip().getAttribute("role")).toBeNull();
  });

  it("is empty before the suite has loaded, then fills the same element", async () => {
    await render({ suite: null });
    const el = chip();
    expect(el).not.toBeNull();
    expect(el.textContent).toBe("");
    await rerender({ suite: { ...suiteWith(), criteria: [{ ...suiteWith().criteria[0], status: "passing" }] } });
    expect(chip()).toBe(el);
    expect(el.textContent).toContain("All criteria passing.");
  });
});

describe("EvalCriteriaPanel test actions", () => {
  it("passes Suggest fix and Generate a second test through to the tests", async () => {
    const onSuggestFix = vi.fn();
    const onGenerateHeldout = vi.fn();
    const base = suiteWith({
      tests: [{ id: "t1", role: "fix", baseline: null, current: null, active_job_id: null, can_suggest_fix: true }],
    });
    base.criteria[0].can_generate_heldout = true;
    await render({ suite: base, onSuggestFix, onGenerateHeldout });
    await act(async () => {
      buttonByText("Suggest fix").click();
    });
    await act(async () => {
      buttonByText("Generate a second test").click();
    });
    expect(onSuggestFix).toHaveBeenCalledWith("t1");
    expect(onGenerateHeldout).toHaveBeenCalledWith("c1");
  });
});

describe("EvalCriteriaPanel focus request hardening", () => {
  const CANDIDATE = {
    version_id: "v3",
    number: 3,
    status: "proposed",
    origin: "suggested",
    strength: "full",
    created_at: "2026-11-22T10:00:00",
    progress: null,
    parent_number: 2,
  };
  const VERIFYING = { ...CANDIDATE, status: "approved_verifying", progress: { done: 0, total: 2 } };
  const LOADED = {
    report: {
      version_id: "v3",
      number: 3,
      status: "proposed",
      strength: "full",
      why: { criterion_name: "Stays on topic", failing_quote: "q", change_summary: "s", touched_section: "t", expected_effect: "e", risk: "r" },
      outcome: null,
    },
    version: { id: "v3", number: 3, text: "new", parent_text: "old" },
    loading: false,
    error: null,
  };

  beforeEach(() => {
    vi.mocked(useReport).mockReturnValue(LOADED);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function approveThenSettle() {
    const onApprove = vi.fn(async () => true);
    await render({ suite: { ...suiteWith(), candidate: CANDIDATE }, onApprove });
    await act(async () => {
      buttonByText("Approve and verify").click();
    });
    return onApprove;
  }

  it("still moves focus when the change arrives promptly and nothing else was focused", async () => {
    const onApprove = await approveThenSettle();
    await rerender({ suite: { ...suiteWith(), candidate: VERIFYING }, onApprove });
    expect(document.activeElement.textContent).toBe("What happened with v3");
  });

  it("drops the request when focus moves to another element of the panel", async () => {
    const onApprove = await approveThenSettle();
    const edit = buttonByText("Edit prompt");
    await act(async () => {
      edit.focus();
    });
    await rerender({ suite: { ...suiteWith(), candidate: VERIFYING }, onApprove });
    expect(document.activeElement).toBe(edit);
  });

  it("drops the request when focus moves outside the panel", async () => {
    const onApprove = await approveThenSettle();
    const outside = document.createElement("input");
    document.body.appendChild(outside);
    await act(async () => {
      outside.focus();
    });
    await rerender({ suite: { ...suiteWith(), candidate: VERIFYING }, onApprove });
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it("drops the request after 15 seconds so a late change cannot steal focus", async () => {
    vi.useFakeTimers();
    const onApprove = await approveThenSettle();
    await act(async () => {
      vi.advanceTimersByTime(15000);
    });
    await rerender({ suite: { ...suiteWith(), candidate: VERIFYING }, onApprove });
    expect(document.activeElement.tagName).not.toBe("H3");
  });

  it("keeps the request alive just under 15 seconds", async () => {
    vi.useFakeTimers();
    const onApprove = await approveThenSettle();
    await act(async () => {
      vi.advanceTimersByTime(14000);
    });
    await rerender({ suite: { ...suiteWith(), candidate: VERIFYING }, onApprove });
    expect(document.activeElement.textContent).toBe("What happened with v3");
  });

  it("does not let the timer of an earlier request clear a newer one", async () => {
    vi.useFakeTimers();
    const onReject = vi.fn(async () => false);
    const onApprove = vi.fn(async () => true);
    await render({ suite: { ...suiteWith(), candidate: CANDIDATE }, onApprove, onReject });
    await act(async () => {
      buttonByText("Approve and verify").click();
    });
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    await rerender({ suite: { ...suiteWith(), candidate: CANDIDATE }, onApprove, onReject });
    await act(async () => {
      buttonByText("Approve and verify").click();
    });
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    await rerender({ suite: { ...suiteWith(), candidate: VERIFYING }, onApprove, onReject });
    expect(document.activeElement.textContent).toBe("What happened with v3");
  });
});
