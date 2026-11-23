// Shared helpers for the Assist console tests (react-dom + act, no testing library).
import { act } from "react";
import { createRoot } from "react-dom/client";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

let mounted = null;

export async function settle(times = 6) {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

export async function mount(element) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted = { container, root };
  await act(async () => {
    root.render(element);
  });
  await settle();
  return container;
}

export async function unmount() {
  if (!mounted) return;
  const { container, root } = mounted;
  mounted = null;
  await act(async () => root.unmount());
  container.remove();
}

export async function click(element) {
  await act(async () => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
}

// React tracks the value itself, so the native setter is used to make onChange fire.
export async function type(input, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

export async function submit(form) {
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await settle();
}

export async function pressKey(target, key) {
  await act(async () => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  });
}

export function buttonByText(container, text) {
  return [...container.querySelectorAll("button")].find((b) => b.textContent.trim() === text);
}

export function buttonByLabel(container, label) {
  return [...container.querySelectorAll("button")].find((b) => b.getAttribute("aria-label") === label);
}


export const SECTIONS = [
  "claims",
  "description",
  "classifications",
  "citations",
  "family",
  "legal_events",
  "landscapes",
];

export function metricsFixture(overrides = {}) {
  return {
    adoption: {
      totals: { page_views: 20, questions: 12, non_bounce_views: 16 },
      sources: { page_views: { live: 0, imported: 20 }, questions: { live: 0, imported: 12 } },
      bounce_rate: 0.2,
      feature_coverage: {
        claims: 0.5,
        description: 0.25,
        classifications: null,
        citations: 0.1,
        family: 0,
        legal_events: 0.3,
        landscapes: null,
      },
      views_by_section: {
        claims: 10,
        description: 8,
        classifications: 0,
        citations: 5,
        family: 4,
        legal_events: 3,
        landscapes: 0,
      },
      help_demand: {
        claims: 30,
        description: 0,
        classifications: null,
        citations: 120,
        family: 0,
        legal_events: 0,
        landscapes: null,
      },
      questions_without_views: { landscapes: 2 },
      task_mix: {
        claim_explain: { count: 6, share: 0.5 },
        navigation_help: { count: 3, share: 0.25 },
        other: { count: 0, share: null },
      },
      struggle: {
        navigation_help_count: 3,
        reask_rate_by_intent: { claim_explain: 0.3333, navigation_help: null, other: 0 },
        refusal_count: 1,
        unverified_citation_rate: null,
        thumbs_down_rate_by_intent: { claim_explain: 0.1667, navigation_help: null, other: null },
      },
    },
    version_comparison: { available: false, reason: "All questions are imported demo data, so versions cannot be compared." },
    data_source_label: "demo dataset",
    sources: { page_views: { live: 0, imported: 20 }, questions: { live: 0, imported: 12 } },
    ...overrides,
  };
}

export const STATUS = {
  agent_id: "assist-1",
  agent_name: "Patent Assist",
  current_version_number: 2,
  run_count: 5,
  flagged_run_count: 3,
  imported_run_count: 3,
};

export function flaggedRun(overrides = {}) {
  return {
    run_id: "run-1",
    position: 1,
    source: "imported",
    session_id: "abcdef1234567890",
    patent_number: "US1234567B2",
    intents: ["claim_explain"],
    sections: ["claims"],
    reasons: ["rated_down", "reasked"],
    created_at: "2026-11-20T10:00:00",
    questions: [],
    ...overrides,
  };
}
