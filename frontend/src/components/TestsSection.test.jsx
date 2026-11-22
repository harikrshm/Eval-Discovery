import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import TestsSection from "./TestsSection";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

function result(verdict, extra = {}) {
  return {
    id: `r-${verdict}`,
    kind: "replay",
    verdict,
    rationale: `Rationale for ${verdict}.`,
    transcript: [
      { role: "user", content: "Is it cheap?" },
      { role: "assistant", content: "It costs 40 dollars." },
    ],
    stale: false,
    prompt_version_id: "pv1",
    created_at: "2026-11-21T10:00:00",
    ...extra,
  };
}

function testRow(id, role, overrides = {}) {
  return {
    id,
    role,
    source_run_id: `run-${id}`,
    baseline: result("fail", { kind: "baseline", rationale: "Original run missed it.", transcript: null }),
    current: null,
    active_job_id: null,
    ...overrides,
  };
}

function suiteFor(overrides = {}) {
  return {
    criterion_id: "c1",
    status: "failing",
    judge_check: { passed: true },
    active_job_id: null,
    tests: [testRow("t1", "fix"), testRow("t2", "heldout")],
    ...overrides,
  };
}

async function render(props) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      createElement(TestsSection, {
        criterionSuite: suiteFor(),
        jobs: {},
        agentName: "Alex",
        userRoleLabel: "Prospect",
        onReplay: vi.fn(),
        onBuildTests: vi.fn(),
        ...props,
      })
    );
  });
}

function buttonByText(text) {
  return [...container.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("TestsSection status and rows", () => {
  it.each([
    ["failing", "Failing"],
    ["passing", "Passing"],
    ["untested", "Untested"],
  ])("shows the %s status as text", async (status, text) => {
    await render({ criterionSuite: suiteFor({ status }) });
    expect(container.textContent).toContain(text);
  });

  it("labels each test and shows the original run and latest replay verdicts as text", async () => {
    await render({
      criterionSuite: suiteFor({
        tests: [
          testRow("t1", "fix", { current: result("pass") }),
          testRow("t2", "heldout"),
        ],
      }),
    });
    const text = container.textContent;
    expect(text).toContain("Fix test");
    expect(text).toContain("Held-out test");
    expect(text).toContain("Original run: Fail");
    expect(text).toContain("Latest replay: Pass");
    expect(text).toContain("Latest replay: Not run");
  });

  it("shows the weaker verification note only when there is just a fix test", async () => {
    await render({ criterionSuite: suiteFor({ tests: [testRow("t1", "fix")] }) });
    expect(container.textContent).toContain(
      "No second failing run, so a fix would be verified more weakly."
    );
    await act(async () => root.unmount());
    container.remove();

    await render({});
    expect(container.textContent).not.toContain("verified more weakly");
  });

  it("shows the low confidence note only when the judge check failed", async () => {
    await render({ criterionSuite: suiteFor({ judge_check: { passed: false } }) });
    expect(container.textContent).toContain("Low confidence");
    await act(async () => root.unmount());
    container.remove();

    await render({ criterionSuite: suiteFor({ judge_check: { passed: true } }) });
    expect(container.textContent).not.toContain("Low confidence");
    await act(async () => root.unmount());
    container.remove();

    await render({ criterionSuite: suiteFor({ judge_check: null }) });
    expect(container.textContent).not.toContain("Low confidence");
  });
});

describe("TestsSection replay", () => {
  it("calls onReplay with the test id", async () => {
    const onReplay = vi.fn();
    await render({ onReplay });
    const buttons = [...container.querySelectorAll("button")].filter((b) => b.textContent.startsWith("Replay"));
    expect(buttons).toHaveLength(2);

    await act(async () => {
      buttons[1].click();
    });
    expect(onReplay).toHaveBeenCalledWith("t2");
  });

  it("disables only the replaying test and relabels it while its job runs", async () => {
    await render({ jobs: { "replay:t1": { status: "running", error: null, result: null } } });
    const replaying = buttonByText("Replaying…");
    expect(replaying.disabled).toBe(true);
    const others = [...container.querySelectorAll("button")].filter((b) => b.textContent.startsWith("Replay") && b !== replaying);
    expect(others).toHaveLength(1);
    expect(others[0].disabled).toBe(false);
  });

  it("treats a queued job (clicked, not yet started) as in flight", async () => {
    await render({ jobs: { "replay:t2": { status: "queued", error: null, result: null } } });
    expect(buttonByText("Replaying…").disabled).toBe(true);
  });

  it("treats a job the suite lists as active as in flight", async () => {
    await render({
      criterionSuite: suiteFor({ tests: [testRow("t1", "fix", { active_job_id: "j1" })] }),
    });
    expect(buttonByText("Replaying…").disabled).toBe(true);
  });

  it("shows a failed replay as an alert with a Retry that replays again", async () => {
    const onReplay = vi.fn();
    await render({
      onReplay,
      jobs: { "replay:t1": { status: "failed", error: "The judge is unavailable.", result: null } },
    });
    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("The judge is unavailable.");

    await act(async () => {
      buttonByText("Retry").click();
    });
    expect(onReplay).toHaveBeenCalledWith("t1");
  });
});

describe("TestsSection details", () => {
  it("reveals the rationale and transcript with agent and role labels on demand", async () => {
    await render({
      criterionSuite: suiteFor({ tests: [testRow("t1", "fix", { current: result("pass") })] }),
    });
    const toggle = buttonByText("Details");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(container.textContent).not.toContain("Rationale for pass.");

    await act(async () => {
      toggle.click();
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const region = document.getElementById(toggle.getAttribute("aria-controls"));
    expect(region.textContent).toContain("Rationale for pass.");
    expect(region.textContent).toContain("Prospect: Is it cheap?");
    expect(region.textContent).toContain("Alex: It costs 40 dollars.");

    await act(async () => {
      toggle.click();
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("says when a test has not been replayed yet", async () => {
    await render({ criterionSuite: suiteFor({ tests: [testRow("t1", "fix")] }) });
    await act(async () => {
      buttonByText("Details").click();
    });
    expect(container.textContent).toContain("Not replayed yet.");
    expect(container.textContent).toContain("Original run missed it.");
  });
});

describe("TestsSection with no tests", () => {
  const empty = () => suiteFor({ status: "untested", tests: [] });

  it("offers Build tests and calls onBuildTests with the criterion id", async () => {
    const onBuildTests = vi.fn();
    await render({ criterionSuite: empty(), onBuildTests });
    expect(container.textContent).toContain("No tests yet.");

    await act(async () => {
      buttonByText("Build tests").click();
    });
    expect(onBuildTests).toHaveBeenCalledWith("c1");
  });

  it("says a build is in progress in a status region, disables Build, and adds a slow notice after 4 s", async () => {
    vi.useFakeTimers();
    await render({
      criterionSuite: empty(),
      jobs: { "build:c1": { status: "running", error: null, result: null } },
    });
    const status = container.querySelector('[role="status"]');
    expect(status.textContent).toContain("Building tests from your saved runs…");
    expect(status.textContent).not.toContain("Still working");
    expect(container.textContent).not.toContain("No tests yet.");
    expect(buttonByText("Build tests")).toBeUndefined();

    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    expect(container.querySelector('[role="status"]').textContent).toContain(
      "Still working. The server may be waking up."
    );
  });

  it("treats a suite-listed active build job as in progress", async () => {
    await render({ criterionSuite: suiteFor({ tests: [], active_job_id: "j9" }) });
    expect(container.querySelector('[role="status"]').textContent).toContain(
      "Building tests from your saved runs…"
    );
  });

  it("explains an already_handled build result", async () => {
    await render({
      criterionSuite: empty(),
      jobs: {
        "build:c1": { status: "done", error: null, result: { already_handled: true, tests_created: 0 } },
      },
    });
    expect(container.textContent).toContain(
      "None of your saved runs fail this criterion, so there is nothing to test yet."
    );
  });

  it("has no special message for an already_built result", async () => {
    await render({
      criterionSuite: empty(),
      jobs: { "build:c1": { status: "done", error: null, result: { already_built: true } } },
    });
    expect(container.textContent).not.toContain("None of your saved runs");
  });

  it("shows a failed build as an alert with a Retry that builds again", async () => {
    const onBuildTests = vi.fn();
    await render({
      criterionSuite: empty(),
      onBuildTests,
      jobs: { "build:c1": { status: "failed", error: "Could not reach the judge.", result: null } },
    });
    expect(container.querySelector('[role="alert"]').textContent).toContain("Could not reach the judge.");

    await act(async () => {
      buttonByText("Retry").click();
    });
    expect(onBuildTests).toHaveBeenCalledWith("c1");
  });
});

describe("TestsSection without a suite entry", () => {
  it("renders nothing until the suite knows the criterion", async () => {
    await render({ criterionSuite: undefined });
    expect(container.textContent).toBe("");
  });
});

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("TestsSection announcements", () => {
  it("renders the latest replay verdict inside an aria-live polite element, even before a result", async () => {
    await render({
      criterionSuite: suiteFor({
        tests: [testRow("t1", "fix", { current: result("pass") }), testRow("t2", "heldout")],
      }),
    });
    const lives = [...container.querySelectorAll('[aria-live="polite"]')];
    expect(lives.map((el) => el.textContent)).toEqual(["Latest replay: Pass", "Latest replay: Not run"]);
  });
});

describe("TestsSection suggest fix", () => {
  const withSuggest = (overrides = {}) =>
    suiteFor({ tests: [testRow("t1", "fix", { can_suggest_fix: true, ...overrides }), testRow("t2", "heldout")] });

  it("shows Suggest fix only on a test the server says can be fixed", async () => {
    await render({ criterionSuite: withSuggest(), onSuggestFix: vi.fn() });
    const buttons = [...container.querySelectorAll("button")].filter((b) => b.textContent.startsWith("Suggest fix"));
    expect(buttons).toHaveLength(1);
    expect(buttons[0].textContent).toContain("Fix test");
  });

  it("never shows Suggest fix on a held-out test", async () => {
    await render({
      criterionSuite: suiteFor({ tests: [testRow("t2", "heldout", { can_suggest_fix: true })] }),
      onSuggestFix: vi.fn(),
    });
    expect(buttonByText("Suggest fix")).toBeUndefined();
  });

  it("shows nothing when can_suggest_fix is false or missing", async () => {
    await render({ criterionSuite: withSuggest({ can_suggest_fix: false }), onSuggestFix: vi.fn() });
    expect(buttonByText("Suggest fix")).toBeUndefined();
  });

  it("calls onSuggestFix with the test id", async () => {
    const onSuggestFix = vi.fn();
    await render({ criterionSuite: withSuggest(), onSuggestFix });
    await act(async () => {
      buttonByText("Suggest fix").click();
    });
    expect(onSuggestFix).toHaveBeenCalledWith("t1");
  });

  it.each(["queued", "running"])("is disabled, busy and labelled Suggesting… while the job is %s", async (status) => {
    await render({
      criterionSuite: withSuggest(),
      onSuggestFix: vi.fn(),
      jobs: { "suggest:t1": { status, error: null, result: null } },
    });
    const pending = buttonByText("Suggesting…");
    expect(pending.disabled).toBe(true);
    expect(pending.getAttribute("aria-busy")).toBe("true");
    expect(buttonByText("Suggest fix")).toBeUndefined();
  });

  it("treats a suggest job the suite lists as in flight", async () => {
    await render({ criterionSuite: withSuggest({ suggest_job_id: "j5" }), onSuggestFix: vi.fn() });
    expect(buttonByText("Suggesting…").disabled).toBe(true);
  });

  it("shows a failed suggestion as an alert with a Retry that suggests again", async () => {
    const onSuggestFix = vi.fn();
    const onReplay = vi.fn();
    await render({
      criterionSuite: withSuggest(),
      onSuggestFix,
      onReplay,
      jobs: { "suggest:t1": { status: "failed", error: "Couldn't write a fix. Try again.", result: null } },
    });
    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("Couldn't write a fix. Try again.");
    const retry = [...container.querySelectorAll("button")].find((b) => b.textContent.includes("suggest a fix"));
    expect(retry.textContent.startsWith("Retry")).toBe(true);

    await act(async () => {
      retry.click();
    });
    expect(onSuggestFix).toHaveBeenCalledWith("t1");
    expect(onReplay).not.toHaveBeenCalled();
  });
});

describe("TestsSection generated held-out test", () => {
  it("labels a generated held-out row", async () => {
    await render({
      criterionSuite: suiteFor({
        tests: [testRow("t1", "fix"), testRow("t2", "heldout", { origin: "generated" })],
      }),
    });
    expect(container.textContent).toContain("Held-out test (generated)");
    expect(container.textContent).toContain("Fix test");
  });

  it("keeps the plain label for a held-out test built from a saved run", async () => {
    await render({
      criterionSuite: suiteFor({ tests: [testRow("t2", "heldout", { origin: "run" })] }),
    });
    expect(container.textContent).toContain("Held-out test");
    expect(container.textContent).not.toContain("(generated)");
  });
});

describe("TestsSection generate a second test", () => {
  const fixOnly = (overrides = {}) =>
    suiteFor({ can_generate_heldout: true, tests: [testRow("t1", "fix")], ...overrides });

  it("shows the button only when the criterion can generate one, and calls it with the criterion id", async () => {
    const onGenerateHeldout = vi.fn();
    await render({ criterionSuite: fixOnly(), onGenerateHeldout });
    await act(async () => {
      buttonByText("Generate a second test").click();
    });
    expect(onGenerateHeldout).toHaveBeenCalledWith("c1");
    await act(async () => root.unmount());
    container.remove();

    await render({ criterionSuite: fixOnly({ can_generate_heldout: false }), onGenerateHeldout });
    expect(buttonByText("Generate a second test")).toBeUndefined();
  });

  it("says it is working in a status region and disables the button while the job runs", async () => {
    await render({
      criterionSuite: fixOnly(),
      onGenerateHeldout: vi.fn(),
      jobs: { "heldout:c1": { status: "running", error: null, result: null } },
    });
    const pending = buttonByText("Generating…");
    expect(pending.disabled).toBe(true);
    expect(pending.getAttribute("aria-busy")).toBe("true");
    expect(container.querySelector('[role="status"]').textContent).toContain("Writing a second test…");
  });

  it("treats a queued job and a suite-listed job as in flight", async () => {
    await render({
      criterionSuite: fixOnly(),
      onGenerateHeldout: vi.fn(),
      jobs: { "heldout:c1": { status: "queued", error: null, result: null } },
    });
    expect(buttonByText("Generating…").disabled).toBe(true);
    await act(async () => root.unmount());
    container.remove();

    await render({ criterionSuite: fixOnly({ heldout_job_id: "j7" }), onGenerateHeldout: vi.fn() });
    expect(buttonByText("Generating…").disabled).toBe(true);
  });

  it("adds a slow notice after 4 s", async () => {
    vi.useFakeTimers();
    await render({
      criterionSuite: fixOnly(),
      onGenerateHeldout: vi.fn(),
      jobs: { "heldout:c1": { status: "running", error: null, result: null } },
    });
    expect(container.textContent).not.toContain("Still working");
    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    expect(container.querySelector('[role="status"]').textContent).toContain(
      "Still working. The server may be waking up."
    );
  });

  it("explains a finished job that wrote no test, and keeps the button for another try", async () => {
    await render({
      criterionSuite: fixOnly(),
      onGenerateHeldout: vi.fn(),
      jobs: { "heldout:c1": { status: "done", error: null, result: { created: false, reason: "agent_handles_it" } } },
    });
    expect(container.textContent).toContain(
      "Couldn't write a test your current prompt fails, so it may already handle this case."
    );
    expect(buttonByText("Generate a second test").disabled).toBe(false);
  });

  it("says the prompt or criterion changed when the job discarded its test", async () => {
    await render({
      criterionSuite: fixOnly(),
      onGenerateHeldout: vi.fn(),
      jobs: { "heldout:c1": { status: "done", error: null, result: { created: false, discarded: true } } },
    });
    expect(container.textContent).toContain(
      "The prompt or criterion changed while the test was being written. Try again."
    );
    expect(container.textContent).not.toContain("may already handle this case");
    expect(buttonByText("Generate a second test").disabled).toBe(false);
  });

  it("does not show that message after a test was created", async () => {
    await render({
      criterionSuite: fixOnly(),
      onGenerateHeldout: vi.fn(),
      jobs: { "heldout:c1": { status: "done", error: null, result: { created: true, test_id: "t9" } } },
    });
    expect(container.textContent).not.toContain("Couldn't write a test");
  });

  it("shows a failure as an alert with a Retry that generates again", async () => {
    const onGenerateHeldout = vi.fn();
    await render({
      criterionSuite: fixOnly(),
      onGenerateHeldout,
      jobs: { "heldout:c1": { status: "failed", error: "Couldn't reach the model.", result: null } },
    });
    expect(container.querySelector('[role="alert"]').textContent).toContain("Couldn't reach the model.");
    await act(async () => {
      buttonByText("Retry").click();
    });
    expect(onGenerateHeldout).toHaveBeenCalledWith("c1");
  });
});
