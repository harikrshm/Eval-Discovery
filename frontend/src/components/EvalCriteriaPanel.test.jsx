import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
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

async function render(props) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      createElement(EvalCriteriaPanel, {
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
        promptEditor: { promptText: "You are Alex.", versionNumber: 2, onSave: vi.fn(async () => {}) },
        ...props,
      })
    );
  });
}

function buttonByText(text) {
  return [...container.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("EvalCriteriaPanel prompt editor and tests", () => {
  it("renders the prompt editor with the current version", async () => {
    await render({});
    expect(container.textContent).toContain("Chat uses prompt v2");
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
    expect(container.textContent).toContain("2 tests are out of date. Run all tests.");

    await act(async () => {
      buttonByText("Run all tests").click();
    });
    expect(onRunAll).toHaveBeenCalledTimes(1);
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
    expect(container.textContent).toContain("Chat uses prompt v2");
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
      el.textContent.includes("out of date")
    );
    expect(banner).toBeDefined();
    expect(banner.textContent).toContain("Run all tests");
  });
});
