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

// Renders new props into the element that `mount` created (same root, so state is kept).
export async function rerender(element) {
  await act(async () => {
    mounted.root.render(element);
  });
  await settle();
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

export const FILTERS = { range: "30d", source: "all", version: null };

const LOOP_STEPS = [
  ["flagged", "Flagged conversations", "done", "3 flagged"],
  ["pattern", "Pattern found", "done", null],
  ["criterion", "Criterion chosen", "current", "0 criteria"],
  ["tests", "Tests built", "todo", null],
  ["fix", "Fix suggested", "todo", null],
  ["review", "Fix reviewed", "todo", null],
  ["verify", "Fix verified", "todo", null],
  ["promoted", "Promoted", "todo", null],
].map(([id, label, state, detail]) => ({ id, label, state, detail }));

// The /assist/console/pipeline payload (backend task 6): the loop stands at "Criterion written".
export function pipelineFixture(overrides = {}) {
  return { steps: LOOP_STEPS, cycle_note: "The loop repeats: a rejected fix sends you back to the fix step.", ...overrides };
}

// A GET /agents/{id}/prompt-versions row (newest first from the server).
export function versionRow(number, overrides = {}) {
  return {
    id: `pv${number}`,
    number,
    origin: "initial",
    status: "superseded",
    strength: null,
    created_at: "2026-11-20T10:00:00",
    is_current: false,
    ...overrides,
  };
}

// A GET /prompt-versions/{id}/report payload whose outcome table has one row per [before, after].
export function reportFor(number, pairs, overrides = {}) {
  return {
    version_id: `pv${number}`,
    number,
    status: "promoted",
    strength: "full",
    why: null,
    outcome: {
      status: "promoted",
      reason: "ok",
      narrative: null,
      explanation_unavailable: false,
      table: pairs.map(([before, after], i) => ({
        test_id: `t${i}`,
        role: i === 0 ? "fix" : "regression",
        criterion_name: "SECRET criterion name",
        before,
        after,
        rationale: "SECRET rationale",
        transcript: [{ role: "user", content: "SECRET transcript" }],
      })),
    },
    ...overrides,
  };
}

// The /assist/console/overview payload (backend task 6), with a few real numbers in it.
export function overviewFixture(overrides = {}) {
  return {
    versions: [1, 2],
    kpis: [
      { id: "questions", label: "Questions", value: 120, unit: "count", delta: 12.5, delta_good: "up", sparkline: [3, 4, 5, 6] },
      { id: "ok_share", label: "Auto-judged OK", value: 75, unit: "percent", delta: -2.5, delta_good: "up", sparkline: [70, null, 80] },
      { id: "open_flagged", label: "Open flagged conversations", value: 4, unit: "count", delta: null, delta_good: "down", sparkline: [0, 1, 2] },
      { id: "live_version", label: "Live version", value: "v2", unit: "version", delta: null, delta_good: "up", sparkline: [] },
    ],
    alerts: [],
    loop: { steps: LOOP_STEPS, cycle_note: "The loop runs on live conversations only." },
    quality_trend: [
      { t: "2026-11-22T10:00:00", avg_grade: 3.5 },
      { t: "2026-11-23T10:00:00", avg_grade: null },
      { t: "2026-11-24T10:00:00", avg_grade: 4.25 },
    ],
    version_markers: [{ at: "2026-11-23T09:00:00", version: 2, source: "live", label: "v2" }],
    ...overrides,
  };
}

// What an empty database returns: well-formed, with nothing in it.
export function emptyOverview() {
  return overviewFixture({
    versions: [1],
    kpis: [
      { id: "questions", label: "Questions", value: 0, unit: "count", delta: null, delta_good: "up", sparkline: [] },
      { id: "ok_share", label: "Auto-judged OK", value: null, unit: "percent", delta: null, delta_good: "up", sparkline: [] },
      { id: "open_flagged", label: "Open flagged conversations", value: 0, unit: "count", delta: null, delta_good: "down", sparkline: [] },
      { id: "live_version", label: "Live version", value: "v1", unit: "version", delta: null, delta_good: "up", sparkline: [] },
    ],
    quality_trend: [],
    version_markers: [],
  });
}

// The /assist/console/trends payload (backend task 5): counts per bucket, whole-range totals and the
// totals of the previous period of the same length (null for the range "all").
export function trendsFixture(overrides = {}) {
  return {
    bucket: "day",
    totals: { page_views: 20, questions: 12, reasked: 3, rated_down: 2 },
    previous: { page_views: 16, questions: 10, reasked: 2, rated_down: 2 },
    points: [
      { t: "2026-11-22T00:00:00", page_views: 5, questions: 3, reasked: 1, rated_down: 0 },
      { t: "2026-11-23T00:00:00", page_views: 7, questions: 4, reasked: 1, rated_down: 1 },
      { t: "2026-11-24T00:00:00", page_views: 8, questions: 5, reasked: 1, rated_down: 1 },
    ],
    version_markers: [
      { at: "2026-11-23T09:00:00", version: 2, source: "live", label: "v2" },
      { at: "2026-11-22T12:00:00", version: 1, source: "demo", label: "v1 (demo)" },
    ],
    ...overrides,
  };
}

// What an empty database returns: well-formed, all zeros, no points.
export function emptyTrends() {
  const zero = { page_views: 0, questions: 0, reasked: 0, rated_down: 0 };
  return { bucket: "day", totals: zero, previous: zero, points: [], version_markers: [] };
}

export function emptyMetrics() {
  return {
    adoption: {
      totals: { page_views: 0, questions: 0, non_bounce_views: 0 },
      bounce_rate: null,
      feature_coverage: {},
      views_by_section: {},
      help_demand: {},
      questions_without_views: {},
      task_mix: {},
      struggle: { reask_rate_by_intent: {}, thumbs_down_rate_by_intent: {} },
    },
    version_comparison: { available: false, reason: "No real questions yet." },
    data_source_label: null,
    sources: { page_views: { live: 0, imported: 0 }, questions: { live: 0, imported: 0 } },
  };
}

// The /assist/console/quality payload (backend task 6) with a few real numbers in it. The average grade
// of the three buckets is (3 x 6 + 4 x 2) / 8 = 3.25, not the plain mean of 3.5.
export function qualityFixture(overrides = {}) {
  return {
    judged: 8,
    unjudged: 3,
    grade_trend: [
      { t: "2026-11-22T00:00:00", avg_grade: 3, n: 6 },
      { t: "2026-11-23T00:00:00", avg_grade: null, n: 0 },
      { t: "2026-11-24T00:00:00", avg_grade: 4, n: 2 },
    ],
    answered: { yes: 5, partly: 2, no: 1 },
    concise_share: 0.75,
    citations_share: null,
    legal: { handled: 2, mishandled: 1, not_applicable: 5 },
    reasons: {
      no_issue: 4,
      missed_question: 2,
      off_topic: 0,
      too_long: 1,
      unsupported_claim: 0,
      bad_citation: 1,
      legal_advice_given: 0,
      refusal_mishandled: 0,
      other: 0,
    },
    ratings: { up: 3, down: 2, no_answer: 1, none: 6 },
    bucket: "day",
    version_markers: [
      { at: "2026-11-23T09:00:00", version: 2, source: "live", label: "v2" },
      { at: "2026-11-22T12:00:00", version: 1, source: "demo", label: "v1 (demo)" },
    ],
    ...overrides,
  };
}

// What an empty database returns: well-formed, with nothing in it.
export function emptyQuality() {
  return {
    judged: 0,
    unjudged: 0,
    grade_trend: [],
    answered: { yes: 0, partly: 0, no: 0 },
    concise_share: null,
    citations_share: null,
    legal: { handled: 0, mishandled: 0, not_applicable: 0 },
    reasons: {
      no_issue: 0,
      missed_question: 0,
      off_topic: 0,
      too_long: 0,
      unsupported_claim: 0,
      bad_citation: 0,
      legal_advice_given: 0,
      refusal_mishandled: 0,
      other: 0,
    },
    ratings: { up: 0, down: 0, no_answer: 0, none: 0 },
    bucket: "day",
    version_markers: [],
  };
}

// The /assist/console/operations payload (backend task 6) with a few real numbers in it: 12 calls over
// three hourly buckets (7 ok, 1 error, 3 rate limited, 1 timeout), the middle bucket with no chat calls.
export function operationsFixture(overrides = {}) {
  return {
    bucket: "hour",
    latency: [
      { t: "2026-11-24T10:37:00", p50_ms: 850, p95_ms: 1900, n: 5 },
      { t: "2026-11-24T11:37:00", p50_ms: null, p95_ms: null, n: 0 },
      { t: "2026-11-24T12:37:00", p50_ms: 1200, p95_ms: 4300, n: 4 },
    ],
    outcomes: [
      { t: "2026-11-24T10:37:00", ok: 5, error: 1, rate_limited: 0, timeout: 0 },
      { t: "2026-11-24T11:37:00", ok: 0, error: 0, rate_limited: 0, timeout: 0 },
      { t: "2026-11-24T12:37:00", ok: 2, error: 0, rate_limited: 3, timeout: 1 },
    ],
    requests: [
      { t: "2026-11-24T10:37:00", n: 6 },
      { t: "2026-11-24T11:37:00", n: 0 },
      { t: "2026-11-24T12:37:00", n: 6 },
    ],
    tokens_by_kind: { chat: 9000, judge: 2500, insights: 1200 },
    totals: { calls: 12, error_rate: 0.1667, rate_limit_rate: 0.25, p50_ms: 1000, p95_ms: 4300, tokens: 12700 },
    judge_queue: {
      waiting_sessions: 3,
      queued_jobs: 1,
      oldest_waiting_minutes: 42,
      judged_last_hour: 7,
      hourly_cap: 20,
      unreadable_sessions: 2,
    },
    recent_failed_jobs: [
      { id: "j1", type: "replay", created_at: "2026-11-24T11:00:00", error: "The judge could not read this conversation." },
      { id: "j2", type: "judge_conversation", created_at: "2026-11-24T09:30:00", error: null },
    ],
    ...overrides,
  };
}

// What an empty database returns: well-formed, with nothing in it.
export function emptyOperations() {
  return {
    bucket: "day",
    latency: [],
    outcomes: [],
    requests: [],
    tokens_by_kind: { chat: 0, judge: 0, insights: 0 },
    totals: { calls: 0, error_rate: null, rate_limit_rate: null, p50_ms: null, p95_ms: null, tokens: 0 },
    judge_queue: {
      waiting_sessions: 0,
      queued_jobs: 0,
      oldest_waiting_minutes: null,
      judged_last_hour: 0,
      hourly_cap: 20,
      unreadable_sessions: 0,
    },
    recent_failed_jobs: [],
  };
}
