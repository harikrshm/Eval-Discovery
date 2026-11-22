import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, getVersions: vi.fn() };
});

import * as api from "../api";
import VersionHistory, { VersionHistoryContainer } from "./VersionHistory";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

// Noon UTC keeps the calendar day the same in every time zone that matters.
function version(n, overrides = {}) {
  return {
    id: `v${n}`,
    number: n,
    origin: "manual",
    status: "superseded",
    strength: null,
    created_at: `2026-11-${String(10 + n).padStart(2, "0")}T12:00:00`,
    is_current: false,
    ...overrides,
  };
}

const VERSIONS = [
  version(4, { origin: "restored", status: "promoted", is_current: true }),
  version(3, { origin: "suggested", status: "failed_verification", strength: "weaker" }),
  version(2, { origin: "suggested", status: "superseded", strength: "full" }),
  version(1, { origin: "initial", status: "promoted" }),
];

function deferred() {
  let resolve;
  const promise = new Promise((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function render(element) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(element);
  });
}

const rerender = (element) =>
  act(async () => {
    root.render(element);
  });

function view(props) {
  return createElement(VersionHistory, {
    versions: VERSIONS,
    currentId: "v4",
    verifying: false,
    onRestore: vi.fn(async () => true),
    busyId: null,
    error: null,
    ...props,
  });
}

const buttons = () => [...container.querySelectorAll("button")];
const buttonByText = (text) => buttons().find((b) => b.textContent.trim() === text);
const toggle = () => buttonByText("Version history");
const rows = () => [...container.querySelectorAll("li")];
const restoreButtons = () => buttons().filter((b) => b.textContent.trim() === "Restore");

async function click(el) {
  await act(async () => {
    el.click();
  });
}

async function key(el, k) {
  await act(async () => {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
  });
}

async function open() {
  await click(toggle());
}

beforeEach(() => {
  document.body.innerHTML = "";
  vi.clearAllMocks();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("VersionHistory", () => {
  it("is a collapsed disclosure until opened", async () => {
    await render(view());
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector("li")).toBeNull();

    await open();
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    const body = document.getElementById(toggle().getAttribute("aria-controls"));
    expect(body).not.toBeNull();
    expect(body.querySelectorAll("li").length).toBe(4);

    await open();
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector("li")).toBeNull();
  });

  it("tells the owner when it opens and closes", async () => {
    const onOpenChange = vi.fn();
    await render(view({ onOpenChange }));
    await open();
    await open();
    expect(onOpenChange.mock.calls).toEqual([[true], [false]]);
  });

  it("lists newest first with origin, status, strength and date, and marks the current one in text", async () => {
    await render(view({ versions: [...VERSIONS].reverse() }));
    await open();
    const text = rows().map((r) => r.textContent);
    expect(text[0]).toContain("v4 · restored · live · Nov 14, 2026");
    expect(text[1]).toContain("v3 · suggested fix · failed verification · verified without a held-out test · Nov 13, 2026");
    expect(text[2]).toContain("v2 · suggested fix · earlier version · held-out verified · Nov 12, 2026");
    expect(text[3]).toContain("v1 · initial · earlier version · Nov 11, 2026");
    expect(text[0]).toContain("Current");
    expect(text.slice(1).some((t) => t.includes("Current"))).toBe(false);
  });

  it("uses plain labels for every origin and status", async () => {
    const all = [
      version(7, { status: "approved_verifying", origin: "suggested" }),
      version(6, { status: "proposed", origin: "suggested" }),
      version(5, { status: "rejected", origin: "suggested" }),
      version(4, { status: "promoted", origin: "manual" }),
      version(3, { status: "failed_verification", origin: "restored" }),
    ];
    await render(view({ versions: all, currentId: "none" }));
    await open();
    const text = rows().map((r) => r.textContent);
    expect(text[0]).toContain("v7 · suggested fix · verifying");
    expect(text[1]).toContain("v6 · suggested fix · proposed");
    expect(text[2]).toContain("v5 · suggested fix · rejected");
    expect(text[3]).toContain("v4 · edited by you · earlier version");
    expect(text[4]).toContain("v3 · restored · failed verification");
  });

  it("renders values as plain text and omits an unusable date", async () => {
    await render(view({ versions: [version(1, { origin: "<b>x</b>", created_at: "garbage", status: "promoted" })], currentId: "v1" }));
    await open();
    expect(container.querySelector("b")).toBeNull();
    expect(rows()[0].textContent).not.toContain("Invalid");
    expect(rows()[0].textContent).toContain("v1 · <b>x</b> · live");
  });

  it("labels a suggestion that was never approved and offers no Restore on it", async () => {
    const all = [
      version(4, { status: "promoted", origin: "manual", is_current: true }),
      version(3, { status: "superseded", origin: "suggested", strength: null }),
      version(2, { status: "superseded", origin: "suggested", strength: "weaker" }),
      version(1, { status: "promoted", origin: "initial" }),
    ];
    await render(view({ versions: all, currentId: "v4" }));
    await open();
    const text = rows().map((r) => r.textContent);
    expect(text[1]).toContain("v3 · suggested fix · never approved");
    expect(text[1]).not.toContain("earlier version");
    expect(text[1]).not.toContain("Restore");
    expect(text[2]).toContain("v2 · suggested fix · earlier version · verified without a held-out test");
    expect(text[2]).toContain("Restore");
    expect(text[3]).toContain("Restore");
  });

  it("does not tell a user to wait for a verification just because only never-approved versions exist", async () => {
    const all = [
      version(3, { status: "approved_verifying", origin: "suggested" }),
      version(2, { status: "superseded", origin: "suggested", strength: null }),
      version(1, { status: "promoted", origin: "initial", is_current: true }),
    ];
    await render(view({ versions: all, currentId: "v1", verifying: true }));
    await open();
    expect(container.textContent).not.toContain("A fix is being verified. Wait for it to finish.");
  });

  it("offers Restore only on promoted, superseded and failed-verification versions that are not current", async () => {
    const all = [
      version(7, { status: "approved_verifying" }),
      version(6, { status: "proposed" }),
      version(5, { status: "rejected" }),
      version(4, { status: "promoted", is_current: true }),
      version(3, { status: "failed_verification" }),
      version(2, { status: "superseded" }),
      version(1, { status: "promoted" }),
    ];
    await render(view({ versions: all, currentId: "v4" }));
    await open();
    const withRestore = rows().filter((r) => r.textContent.includes("Restore")).map((r) => r.textContent.slice(0, 2));
    expect(withRestore).toEqual(["v3", "v2", "v1"]);
  });

  it("asks for confirmation before restoring, then calls onRestore with the version id", async () => {
    const onRestore = vi.fn(async () => true);
    await render(view({ onRestore }));
    await open();
    await click(restoreButtons()[0]); // v3

    expect(onRestore).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Restore v3? Chat will use it right away.");
    expect(buttonByText("Confirm restore")).toBeDefined();
    expect(document.activeElement).toBe(buttonByText("Confirm restore"));

    await click(buttonByText("Confirm restore"));
    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(onRestore).toHaveBeenCalledWith("v3");
  });

  it("Cancel returns focus to that version's Restore button", async () => {
    const onRestore = vi.fn();
    await render(view({ onRestore }));
    await open();
    await click(restoreButtons()[1]); // v2
    await click(buttonByText("Cancel"));

    expect(onRestore).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("Confirm restore");
    expect(document.activeElement).toBe(restoreButtons()[1]);
    expect(document.activeElement.closest("li").textContent).toContain("v2");
  });

  it("Escape cancels the confirmation without reaching the window", async () => {
    const onWindowKey = vi.fn();
    window.addEventListener("keydown", onWindowKey);
    await render(view());
    await open();
    await click(restoreButtons()[0]);
    await key(buttonByText("Confirm restore"), "Escape");

    expect(container.textContent).not.toContain("Confirm restore");
    expect(document.activeElement).toBe(restoreButtons()[0]);
    expect(onWindowKey).not.toHaveBeenCalled();
    window.removeEventListener("keydown", onWindowKey);
  });

  it("disables the confirmation from the click, ignores a second click, and returns focus to the toggle on success", async () => {
    const d = deferred();
    const onRestore = vi.fn(() => d.promise);
    await render(view({ onRestore }));
    await open();
    await click(restoreButtons()[0]);
    const confirm = buttonByText("Confirm restore");
    await click(confirm);

    expect(confirm.disabled).toBe(true);
    expect(buttonByText("Cancel").disabled).toBe(true);
    expect(document.activeElement).toBe(toggle());
    await click(confirm);
    expect(onRestore).toHaveBeenCalledTimes(1);

    await act(async () => d.resolve(true));
    expect(container.textContent).not.toContain("Confirm restore");
    expect(document.activeElement).toBe(toggle());
    expect(container.querySelector('[role="status"]').textContent).toContain("Restored v3");
  });

  it("keeps the announcement region mounted before it has text", async () => {
    await render(view());
    const region = container.querySelector('[role="status"]');
    expect(region).not.toBeNull();
    expect(region.textContent).toBe("");
  });

  it("on a refused restore, closes the confirmation and puts focus back on that Restore button", async () => {
    const onRestore = vi.fn(async () => false);
    await render(view({ onRestore }));
    await open();
    await click(restoreButtons()[0]);
    await click(buttonByText("Confirm restore"));

    expect(container.textContent).not.toContain("Confirm restore");
    expect(document.activeElement).toBe(restoreButtons()[0]);
    expect(container.querySelector('[role="status"]').textContent).toBe("");
  });

  it("does not steal focus when the user moved elsewhere while the restore ran", async () => {
    const d = deferred();
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    await render(view({ onRestore: () => d.promise }));
    await open();
    await click(restoreButtons()[0]);
    await click(buttonByText("Confirm restore"));
    outside.focus();
    await act(async () => d.resolve(true));
    expect(document.activeElement).toBe(outside);
  });

  it("disables every Restore while another restore is in flight", async () => {
    await render(view({ busyId: "v2" }));
    await open();
    expect(restoreButtons().length).toBe(3);
    expect(restoreButtons().every((b) => b.disabled)).toBe(true);
  });

  it("disables Restore and says why while a fix is being verified", async () => {
    await render(view({ verifying: true }));
    await open();
    expect(restoreButtons().every((b) => b.disabled)).toBe(true);
    expect(container.textContent).toContain("A fix is being verified. Wait for it to finish.");
    const note = document.getElementById(restoreButtons()[0].getAttribute("aria-describedby"));
    expect(note.textContent).toContain("A fix is being verified");
  });

  it("shows the server's refusal in an alert", async () => {
    await render(view({ error: "A fix is being verified. Wait for it to finish." }));
    await open();
    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toBe("A fix is being verified. Wait for it to finish.");
  });

  it("shows loading, a load error with Retry, and an empty list", async () => {
    const onRetry = vi.fn();
    await render(view({ versions: null, loading: true }));
    await open();
    expect(container.textContent).toContain("Loading versions…");

    await rerender(view({ versions: null, loading: false, loadError: "Backend down", onRetry }));
    expect(container.querySelector('[role="alert"]').textContent).toContain("Couldn't load the version history. Backend down");
    await click(buttonByText("Retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);

    await rerender(view({ versions: [] }));
    expect(container.textContent).toContain("No versions yet.");
  });

  it("does not throw if the restore handler throws", async () => {
    const onRestore = vi.fn(async () => {
      throw new Error("boom");
    });
    await render(view({ onRestore }));
    await open();
    await click(restoreButtons()[0]);
    await click(buttonByText("Confirm restore"));
    expect(container.textContent).not.toContain("Confirm restore");
    expect(document.activeElement).toBe(restoreButtons()[0]);
  });
});

describe("VersionHistoryContainer", () => {
  function suite(overrides = {}) {
    return { prompt_version_id: "v4", candidate: null, ...overrides };
  }

  function mountContainer(props) {
    return createElement(VersionHistoryContainer, {
      agentId: "a1",
      suite: suite(),
      jobs: {},
      onRestore: vi.fn(async () => true),
      ...props,
    });
  }

  beforeEach(() => {
    api.getVersions.mockResolvedValue(VERSIONS);
  });

  it("does not fetch until the section is opened, then loads and lists", async () => {
    await render(mountContainer());
    expect(api.getVersions).not.toHaveBeenCalled();
    await open();
    expect(api.getVersions).toHaveBeenCalledWith("a1");
    expect(rows().length).toBe(4);
  });

  it("reloads when the suite's version or the candidate status changes while open", async () => {
    await render(mountContainer());
    await open();
    expect(api.getVersions).toHaveBeenCalledTimes(1);

    await rerender(mountContainer({ suite: suite({ candidate: { version_id: "v5", status: "approved_verifying", number: 5 } }) }));
    expect(api.getVersions).toHaveBeenCalledTimes(2);
    await rerender(mountContainer({ suite: suite({ candidate: { version_id: "v5", status: "failed_verification", number: 5 } }) }));
    expect(api.getVersions).toHaveBeenCalledTimes(3);
    await rerender(mountContainer({ suite: suite({ prompt_version_id: "v6" }) }));
    expect(api.getVersions).toHaveBeenCalledTimes(4);
    // Unrelated suite changes do not reload.
    await rerender(mountContainer({ suite: suite({ prompt_version_id: "v6", stale_count: 2 }) }));
    expect(api.getVersions).toHaveBeenCalledTimes(4);
  });

  it("disables Restore while a fix verifies", async () => {
    await render(mountContainer({ suite: suite({ candidate: { version_id: "v5", status: "approved_verifying", number: 5 } }) }));
    await open();
    expect(restoreButtons().every((b) => b.disabled)).toBe(true);
  });

  it("restores through onRestore, then reloads the list", async () => {
    const onRestore = vi.fn(async () => true);
    await render(mountContainer({ onRestore }));
    await open();
    await click(restoreButtons()[0]);
    await click(buttonByText("Confirm restore"));
    expect(onRestore).toHaveBeenCalledWith("v3");
    expect(api.getVersions).toHaveBeenCalledTimes(2);
    expect(document.activeElement).toBe(toggle());
  });

  it("reloads after a failed restore too, and shows the hook's error for that restore only", async () => {
    const onRestore = vi.fn(async () => false);
    await render(mountContainer({ onRestore }));
    await open();
    await click(restoreButtons()[0]);
    await click(buttonByText("Confirm restore"));
    expect(api.getVersions).toHaveBeenCalledTimes(2);

    await rerender(
      mountContainer({ onRestore, jobs: { "version:v3": { status: "failed", error: "A fix is being verified. Wait for it to finish." } } })
    );
    expect(container.querySelector('[role="alert"]').textContent).toBe("A fix is being verified. Wait for it to finish.");

    // An error from some other action on a version this section did not restore is not shown here.
    await rerender(mountContainer({ onRestore, jobs: { "version:v2": { status: "failed", error: "Approve failed" } } }));
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("clears the restore error when the section collapses and when a new confirmation opens", async () => {
    const onRestore = vi.fn(async () => false);
    const failedJobs = { "version:v3": { status: "failed", error: "This version can't be restored." } };
    await render(mountContainer({ onRestore }));
    await open();
    await click(restoreButtons()[0]);
    await click(buttonByText("Confirm restore"));
    await rerender(mountContainer({ onRestore, jobs: failedJobs }));
    expect(container.querySelector('[role="alert"]').textContent).toBe("This version can't be restored.");

    // A new confirmation opens: the old refusal goes away.
    await click(restoreButtons()[0]);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(buttonByText("Confirm restore")).toBeDefined();
    await click(buttonByText("Cancel"));

    // Same job state again (nothing new happened), then collapse and reopen.
    await rerender(mountContainer({ onRestore, jobs: { ...failedJobs } }));
    await click(restoreButtons()[0]);
    await click(buttonByText("Confirm restore"));
    await rerender(mountContainer({ onRestore, jobs: { "version:v3": { status: "failed", error: "Again." } } }));
    expect(container.querySelector('[role="alert"]').textContent).toBe("Again.");
    await open(); // collapse
    await open(); // reopen
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("treats the restoring version's queued job as busy", async () => {
    const onRestore = vi.fn(() => new Promise(() => {}));
    await render(mountContainer({ onRestore }));
    await open();
    await click(restoreButtons()[0]);
    await click(buttonByText("Confirm restore"));
    await rerender(mountContainer({ onRestore, jobs: { "version:v3": { status: "queued", error: null } } }));
    expect(restoreButtons().every((b) => b.disabled)).toBe(true);
  });
});
