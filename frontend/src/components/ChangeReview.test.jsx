import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";

vi.mock("../hooks/useReport", () => ({ useReport: vi.fn() }));

import { useReport } from "../hooks/useReport";
import ChangeReview, { ChangeReviewContainer } from "./ChangeReview";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

const WHY = {
  criterion_name: "Stays on topic",
  failing_quote: "Sure, I can also help with your taxes.",
  change_summary: "Tell the agent to decline unrelated requests.",
  touched_section: "The rules section",
  expected_effect: "The agent politely redirects.",
  risk: "It may refuse borderline questions.",
};

function candidate(overrides = {}) {
  return {
    version_id: "v3",
    number: 3,
    status: "proposed",
    origin: "suggested",
    strength: "full",
    created_at: "2026-11-22T10:00:00",
    progress: null,
    parent_number: 2,
    ...overrides,
  };
}

const VERSION = { id: "v3", number: 3, text: "Line A\nLine NEW\nLine C", parent_text: "Line A\nLine OLD\nLine C" };

function row(overrides = {}) {
  return {
    test_id: "t1",
    role: "fix",
    criterion_name: "Stays on topic",
    before: "fail",
    after: "pass",
    rationale: "It stayed on topic now.",
    transcript: [
      { role: "user", content: "Do my taxes" },
      { role: "assistant", content: "I can only help with this product." },
    ],
    ...overrides,
  };
}

function reportWith(outcome, extra = {}) {
  return {
    version_id: "v3",
    number: 3,
    status: outcome?.status ?? "proposed",
    strength: "full",
    why: WHY,
    outcome,
    ...extra,
  };
}

function outcome(overrides = {}) {
  return {
    status: "promoted",
    reason: "All required tests passed.",
    table: [
      row(),
      row({ test_id: "t2", role: "heldout", before: "fail", after: "pass", rationale: "Held rationale", transcript: [] }),
      row({ test_id: "t3", role: "regression", before: "pass", after: "pass", rationale: "Still fine", transcript: [] }),
    ],
    narrative: { explanation: "The fix worked.", next_step: "Run all tests.", cited_test_ids: ["t1"] },
    explanation_unavailable: false,
    ...overrides,
  };
}

async function render(props, element = ChangeReview) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      createElement(element, {
        candidate: candidate(),
        version: VERSION,
        report: reportWith(null),
        reportLoading: false,
        reportError: null,
        busy: false,
        error: null,
        onApprove: vi.fn(),
        onReject: vi.fn(),
        agentName: "Alex",
        userRoleLabel: "Prospect",
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
  vi.mocked(useReport).mockReset();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("ChangeReview proposed state", () => {
  it("shows the heading and the parent sentence", async () => {
    await render({});
    expect(container.querySelector("h3").textContent).toBe("Suggested fix v3");
    expect(container.textContent).toContain("Chat keeps using v2 until this fix is verified.");
  });

  it("renders the line diff with prefixes and screen reader labels", async () => {
    await render({});
    const items = [...container.querySelectorAll('[aria-label="Prompt changes"] li')];
    expect(items.map((li) => li.textContent.replace(/^(Added line|Removed line)/, ""))).toEqual([
      "  Line A",
      "- Line OLD",
      "+ Line NEW",
      "  Line C",
    ]);
    expect(items[1].querySelector(".sr-only").textContent).toBe("Removed line");
    expect(items[2].querySelector(".sr-only").textContent).toBe("Added line");
    expect(items[0].querySelector(".sr-only")).toBeNull();
  });

  it("shows the why section from the report", async () => {
    await render({ report: reportWith(null) });
    const text = container.textContent;
    expect(text).toContain("Why this change");
    for (const value of Object.values(WHY)) expect(text).toContain(value);
    expect(container.querySelector("blockquote").textContent).toBe(WHY.failing_quote);
  });

  it("renders untrusted text as plain text, never as markup", async () => {
    const evil = "<img src=x onerror=alert(1)> **bold**";
    await render({ report: reportWith(null, { why: { ...WHY, change_summary: evil } }) });
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain(evil);
  });

  it("calls the handlers from Approve and Reject", async () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    await render({ onApprove, onReject });
    await act(async () => {
      buttonByText("Approve and verify").click();
    });
    await act(async () => {
      buttonByText("Reject").click();
    });
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it("disables both buttons while busy", async () => {
    await render({ busy: true });
    expect(buttonByText("Approve and verify").disabled).toBe(true);
    expect(buttonByText("Reject").disabled).toBe(true);
  });

  it("shows an alert for an error", async () => {
    await render({ error: "A fix is being verified. Wait for it to finish." });
    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("A fix is being verified.");
  });

  it("shows no alert without an error", async () => {
    await render({});
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("shows a loading status and no actions while the report loads", async () => {
    await render({ report: null, version: null, reportLoading: true });
    expect(container.querySelector('[role="status"]').textContent).toBe("Loading the suggested fix…");
    expect(buttonByText("Approve and verify")).toBeUndefined();
  });

  it("shows an alert with the error text when the report cannot load", async () => {
    await render({ report: null, version: null, reportError: "Backend down" });
    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("Couldn't load the suggested fix.");
    expect(alert.textContent).toContain("Backend down");
    expect(buttonByText("Retry")).toBeUndefined();
  });

  it("keeps one status region that exists before its text changes", async () => {
    await render({ report: null, version: null, reportLoading: true });
    const region = container.querySelector('[role="status"]');
    await act(async () => {
      root.render(
        createElement(ChangeReview, {
          candidate: candidate(),
          version: VERSION,
          report: reportWith(null),
          reportLoading: false,
          reportError: null,
          busy: false,
          error: null,
          onApprove: vi.fn(),
          onReject: vi.fn(),
        })
      );
    });
    expect(container.querySelector('[role="status"]')).toBe(region);
    expect(region.textContent).toBe("");
  });
});

describe("ChangeReview verifying state", () => {
  const verifying = candidate({ status: "approved_verifying", progress: { done: 1, total: 3 } });

  it("announces the progress in a status region", async () => {
    await render({ candidate: verifying, report: reportWith(null) });
    const status = container.querySelector('[role="status"]');
    expect(status.textContent).toBe("Verifying v3: 1 of 3 tests done. Chat still uses v2.");
    expect(buttonByText("Approve and verify")).toBeUndefined();
    expect(container.querySelector('[aria-label="Prompt changes"]')).toBeNull();
  });

  it("updates the same region as progress changes", async () => {
    await render({ candidate: verifying });
    const region = container.querySelector('[role="status"]');
    await act(async () => {
      root.render(
        createElement(ChangeReview, {
          candidate: { ...verifying, progress: { done: 2, total: 3 } },
          version: VERSION,
          report: reportWith(null),
          reportLoading: false,
          reportError: null,
          busy: false,
          error: null,
          onApprove: vi.fn(),
          onReject: vi.fn(),
        })
      );
    });
    expect(container.querySelector('[role="status"]')).toBe(region);
    expect(region.textContent).toContain("2 of 3 tests done");
  });

  it("handles missing progress without crashing", async () => {
    await render({ candidate: { ...verifying, progress: null } });
    expect(container.querySelector('[role="status"]').textContent).toContain("Verifying v3");
  });
});

describe("ChangeReview resolved states", () => {
  const promoted = candidate({ status: "promoted" });
  const failed = candidate({ status: "failed_verification" });

  it("shows the before -> after table with labels and verdict words", async () => {
    await render({ candidate: promoted, report: reportWith(outcome()) });
    const headers = [...container.querySelectorAll("th")].map((th) => th.textContent);
    expect(headers).toEqual(["Test", "Before", "After"]);
    const bodyRows = [...container.querySelectorAll("tbody tr")].filter((tr) => tr.querySelector("td")?.textContent);
    const text = bodyRows.map((tr) => tr.textContent);
    expect(text[0]).toContain("Fix test");
    expect(text[0]).toContain("Fail");
    expect(text[0]).toContain("Pass");
    expect(text[1]).toContain("Held-out test");
    expect(text[2]).toContain("Previously passing");
  });

  it("falls back to Not run for a missing after verdict", async () => {
    const o = outcome({ table: [row({ after: null })] });
    await render({ candidate: failed, report: reportWith(o) });
    expect(container.querySelector("tbody").textContent).toContain("Not run");
  });

  it("reveals a row's rationale and transcript through Details", async () => {
    await render({ candidate: promoted, report: reportWith(outcome()) });
    expect(container.textContent).not.toContain("It stayed on topic now.");
    const details = buttonByText("Details");
    expect(details.getAttribute("aria-expanded")).toBe("false");
    await act(async () => {
      details.click();
    });
    expect(details.getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("It stayed on topic now.");
    expect(container.textContent).toContain("Prospect: Do my taxes");
    expect(container.textContent).toContain("Alex: I can only help with this product.");
  });

  it("shows the promoted outcome line, the stored status and the strength note", async () => {
    await render({ candidate: promoted, report: reportWith(outcome()) });
    const text = container.textContent;
    expect(text).toContain("v3 is now live.");
    expect(text).toContain("Promoted");
    expect(text).toContain("Verified with a held-out test.");
    expect(text).not.toContain("Chat still uses");
  });

  it("shows the failed outcome with the reason and that chat still uses the parent", async () => {
    const o = outcome({ status: "failed_verification", reason: "The fix test still fails." });
    await render({ candidate: failed, report: reportWith(o, { strength: "weaker" }) });
    const text = container.textContent;
    expect(text).toContain("v3 was not promoted: The fix test still fails.");
    expect(text).toContain("Not promoted");
    expect(text).toContain("Chat still uses v2.");
    expect(text).toContain("Verified without a held-out test (weaker).");
  });

  it("trusts the stored status, not the narrative prose", async () => {
    const o = outcome({
      status: "failed_verification",
      reason: "The fix test still fails.",
      narrative: { explanation: "v3 is now live and everything passed.", next_step: "Nothing.", cited_test_ids: [] },
    });
    await render({ candidate: failed, report: reportWith(o) });
    const text = container.textContent;
    expect(text).toContain("v3 was not promoted: The fix test still fails.");
    expect(text).toContain("v3 is now live and everything passed.");
    expect(text).toContain("Not promoted");
  });

  it("shows the narrative explanation and next step", async () => {
    await render({ candidate: promoted, report: reportWith(outcome()) });
    expect(container.textContent).toContain("The fix worked.");
    expect(container.textContent).toContain("Run all tests.");
    expect(container.textContent).not.toContain("Writing the explanation");
  });

  it("shows a status while the narrative is pending", async () => {
    const o = outcome({ narrative: null });
    await render({ candidate: promoted, report: reportWith(o) });
    const status = [...container.querySelectorAll('[role="status"]')].find((el) =>
      el.textContent.includes("Writing the explanation…")
    );
    expect(status).toBeDefined();
  });

  it("keeps the same status region when the narrative arrives", async () => {
    const pending = reportWith(outcome({ narrative: null }));
    await render({ candidate: promoted, report: pending });
    const region = container.querySelector('[role="status"]');
    expect(region.textContent).toBe("Writing the explanation…");
    await act(async () => {
      root.render(
        createElement(ChangeReview, {
          candidate: promoted,
          version: VERSION,
          report: reportWith(outcome()),
          reportLoading: false,
          reportError: null,
          busy: false,
          error: null,
          onApprove: vi.fn(),
          onReject: vi.fn(),
        })
      );
    });
    expect(container.querySelector('[role="status"]')).toBe(region);
    expect(region.textContent).toBe("");
    expect(container.textContent).toContain("The fix worked.");
  });

  it("says the explanation is unavailable and still shows the table", async () => {
    const o = outcome({ narrative: null, explanation_unavailable: true });
    await render({ candidate: promoted, report: reportWith(o) });
    expect(container.textContent).toContain("Explanation unavailable.");
    expect(container.textContent).not.toContain("Writing the explanation");
    expect(container.querySelector("table")).not.toBeNull();
  });

  it("shows a loading status while the resolved report loads", async () => {
    await render({ candidate: promoted, report: null, version: null, reportLoading: true });
    expect(container.querySelector('[role="status"]').textContent).toBe("Loading the suggested fix…");
  });

  it("still shows the outcome line from the candidate when the report has no outcome yet", async () => {
    await render({ candidate: failed, report: reportWith(null) });
    expect(container.textContent).toContain("v3 was not promoted");
    expect(container.querySelector("table")).toBeNull();
  });
});

describe("ChangeReviewContainer", () => {
  function mockReport(value) {
    vi.mocked(useReport).mockReturnValue({
      report: null,
      version: null,
      loading: false,
      error: null,
      ...value,
    });
  }

  function containerProps(overrides = {}) {
    return { candidate: candidate(), jobs: {}, onApprove: vi.fn(), onReject: vi.fn(), ...overrides };
  }

  it("fetches the report for the candidate with the hook enabled", async () => {
    mockReport({ report: reportWith(null), version: VERSION });
    await render(containerProps(), ChangeReviewContainer);
    expect(useReport).toHaveBeenCalledWith("v3", { enabled: true });
    expect(container.textContent).toContain("Suggested fix v3");
  });

  it("passes the loading and error states through", async () => {
    mockReport({ loading: true });
    await render(containerProps(), ChangeReviewContainer);
    expect(container.textContent).toContain("Loading the suggested fix…");
  });

  it("approves and rejects by version id", async () => {
    mockReport({ report: reportWith(null), version: VERSION });
    const props = containerProps();
    await render(props, ChangeReviewContainer);
    await act(async () => {
      buttonByText("Approve and verify").click();
    });
    await act(async () => {
      buttonByText("Reject").click();
    });
    expect(props.onApprove).toHaveBeenCalledWith("v3");
    expect(props.onReject).toHaveBeenCalledWith("v3");
  });

  it("is busy while the version action is queued or running", async () => {
    mockReport({ report: reportWith(null), version: VERSION });
    await render(containerProps({ jobs: { "version:v3": { status: "queued", error: null } } }), ChangeReviewContainer);
    expect(buttonByText("Approve and verify").disabled).toBe(true);
    expect(buttonByText("Reject").disabled).toBe(true);
  });

  it("shows the failed action's error and re-enables the buttons", async () => {
    mockReport({ report: reportWith(null), version: VERSION });
    await render(
      containerProps({ jobs: { "version:v3": { status: "failed", error: "Nope, it moved on." } } }),
      ChangeReviewContainer
    );
    expect(container.querySelector('[role="alert"]').textContent).toContain("Nope, it moved on.");
    expect(buttonByText("Approve and verify").disabled).toBe(false);
  });
});
