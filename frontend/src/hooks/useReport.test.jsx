import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, getVersion: vi.fn(), getReport: vi.fn() };
});

import * as api from "../api";
import { useReport } from "./useReport";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const VERSION = { id: "v2", number: 2, text: "new prompt", parent_text: "old prompt", status: "promoted" };

function report({ narrative = null, unavailable = false, outcome = true } = {}) {
  return {
    version_id: "v2",
    number: 2,
    status: "promoted",
    strength: "full",
    why: { criterion_name: "Pricing" },
    outcome: outcome
      ? { status: "promoted", reason: "ok", table: [], narrative, explanation_unavailable: unavailable }
      : null,
  };
}

const NARRATIVE = { explanation: "It now answers.", next_step: "Run all tests.", cited_test_ids: ["t1"] };

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function mount(initial) {
  const holder = { current: null };
  const record = (value) => {
    holder.current = value;
  };
  function Probe({ versionId, enabled }) {
    record(useReport(versionId, { enabled }));
    return null;
  }
  const container = document.createElement("div");
  const root = createRoot(container);
  const render = async (versionId, enabled = true) => {
    await act(async () => {
      root.render(createElement(Probe, { versionId, enabled }));
    });
  };
  return {
    holder,
    render: (enabled) => render(initial, enabled),
    rerender: render,
    unmount: () => act(() => root.unmount()),
  };
}

const tick = (ms) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

describe("useReport", () => {
  let errorSpy;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    api.getVersion.mockResolvedValue(VERSION);
    api.getReport.mockResolvedValue(report({ narrative: NARRATIVE }));
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
    vi.useRealTimers();
  });

  it("is idle without a version id or when disabled", async () => {
    const none = mount(null);
    await none.render();
    expect(none.holder.current).toEqual({ report: null, version: null, loading: false, error: null, gaveUp: false });
    await none.unmount();

    const off = mount("v2");
    await off.render(false);
    expect(off.holder.current).toEqual({ report: null, version: null, loading: false, error: null, gaveUp: false });
    expect(api.getVersion).not.toHaveBeenCalled();
    expect(api.getReport).not.toHaveBeenCalled();
    await off.unmount();
  });

  it("loads the version and the report together", async () => {
    const version = deferred();
    api.getVersion.mockReturnValue(version.promise);
    const view = mount("v2");
    await view.render();

    expect(api.getVersion).toHaveBeenCalledWith("v2");
    expect(api.getReport).toHaveBeenCalledWith("v2");
    expect(view.holder.current.loading).toBe(true);
    expect(view.holder.current.report).toBeNull();

    await act(async () => version.resolve(VERSION));

    expect(view.holder.current.loading).toBe(false);
    expect(view.holder.current.error).toBeNull();
    expect(view.holder.current.version).toEqual(VERSION);
    expect(view.holder.current.report).toEqual(report({ narrative: NARRATIVE }));
    await view.unmount();
  });

  it("does not poll once the narrative is there", async () => {
    const view = mount("v2");
    await view.render();

    await tick(10000);

    expect(api.getReport).toHaveBeenCalledTimes(1);
    await view.unmount();
  });

  it("does not poll when there is no outcome yet", async () => {
    api.getReport.mockResolvedValue(report({ outcome: false }));
    const view = mount("v2");
    await view.render();

    await tick(10000);

    expect(api.getReport).toHaveBeenCalledTimes(1);
    await view.unmount();
  });

  it("polls every 3000 ms until a narrative appears, then stops", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();
    expect(api.getReport).toHaveBeenCalledTimes(1);

    await tick(2999);
    expect(api.getReport).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(api.getReport).toHaveBeenCalledTimes(2);
    await tick(3000);
    expect(api.getReport).toHaveBeenCalledTimes(3);
    expect(view.holder.current.report.outcome.narrative).toBeNull();

    api.getReport.mockResolvedValue(report({ narrative: NARRATIVE }));
    await tick(3000);
    expect(api.getReport).toHaveBeenCalledTimes(4);
    expect(view.holder.current.report.outcome.narrative).toEqual(NARRATIVE);

    await tick(30000);
    expect(api.getReport).toHaveBeenCalledTimes(4);
    expect(api.getVersion).toHaveBeenCalledTimes(1); // the version text never changes
    await view.unmount();
  });

  it("stops polling when the explanation is unavailable", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();

    api.getReport.mockResolvedValue(report({ unavailable: true }));
    await tick(3000);
    expect(view.holder.current.report.outcome.explanation_unavailable).toBe(true);

    await tick(30000);
    expect(api.getReport).toHaveBeenCalledTimes(2);
    await view.unmount();
  });

  it("gives up after 120000 ms", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();

    await tick(120000);
    const callsAtTimeout = api.getReport.mock.calls.length;
    expect(callsAtTimeout).toBeGreaterThan(1);
    expect(callsAtTimeout).toBeLessThanOrEqual(41);

    await tick(60000);
    expect(api.getReport).toHaveBeenCalledTimes(callsAtTimeout);
    await view.unmount();
  });

  it("reports gaveUp once the time limit stops polling with the narrative still missing", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();
    expect(view.holder.current.gaveUp).toBe(false);

    await tick(119000);
    expect(view.holder.current.gaveUp).toBe(false);
    await tick(1000);
    expect(view.holder.current.gaveUp).toBe(true);
    expect(view.holder.current.report).not.toBeNull();
    await view.unmount();
  });

  it("does not report gaveUp when the narrative arrived in time", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();
    api.getReport.mockResolvedValue(report({ narrative: NARRATIVE }));
    await tick(3000);

    await tick(130000);
    expect(view.holder.current.gaveUp).toBe(false);
    await view.unmount();
  });

  it("does not report gaveUp when the explanation was marked unavailable", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();
    api.getReport.mockResolvedValue(report({ unavailable: true }));
    await tick(3000);

    await tick(130000);
    expect(view.holder.current.gaveUp).toBe(false);
    await view.unmount();
  });

  it("clears gaveUp for another version", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();
    await tick(121000);
    expect(view.holder.current.gaveUp).toBe(true);

    await view.rerender("v3");
    expect(view.holder.current.gaveUp).toBe(false);
    await view.unmount();
  });

  it("keeps polling through a failed poll request", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();

    api.getReport.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await tick(3000);
    expect(view.holder.current.error).toBeNull();
    expect(view.holder.current.report).not.toBeNull();

    api.getReport.mockResolvedValue(report({ narrative: NARRATIVE }));
    await tick(3000);
    expect(view.holder.current.report.outcome.narrative).toEqual(NARRATIVE);
    await view.unmount();
  });

  it("reports the server message when the first load fails", async () => {
    api.getReport.mockRejectedValue(new api.ApiError("Version not found.", 404));
    const view = mount("v2");
    await view.render();

    expect(view.holder.current.error).toBe("Version not found.");
    expect(view.holder.current.loading).toBe(false);
    expect(view.holder.current.report).toBeNull();
    await tick(30000);
    expect(api.getReport).toHaveBeenCalledTimes(1);
    await view.unmount();
  });

  it("turns a raw network failure into friendly copy", async () => {
    api.getVersion.mockRejectedValue(new TypeError("Failed to fetch"));
    const view = mount("v2");
    await view.render();

    expect(view.holder.current.error).toBe("Couldn't reach the server. Check your connection, then try again.");
    await view.unmount();
  });

  it("aborts polling on unmount and updates nothing afterwards", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();
    const pending = deferred();
    api.getReport.mockReturnValue(pending.promise);
    await tick(3000);
    expect(api.getReport).toHaveBeenCalledTimes(2);

    await view.unmount();
    await act(async () => pending.resolve(report({ narrative: NARRATIVE })));
    await tick(60000);

    expect(api.getReport).toHaveBeenCalledTimes(2);
  });

  it("ignores a first load that resolves after unmount", async () => {
    const slow = deferred();
    api.getVersion.mockReturnValue(slow.promise);
    const view = mount("v2");
    await view.render();

    await view.unmount();
    await act(async () => slow.resolve(VERSION));
    await tick(10000);

    expect(api.getReport).toHaveBeenCalledTimes(1);
  });

  it("drops the old version's data and polling when the version changes", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();
    expect(view.holder.current.report.version_id).toBe("v2");

    const slow = deferred();
    api.getVersion.mockReturnValue(slow.promise);
    api.getReport.mockResolvedValue({ ...report({ narrative: NARRATIVE }), version_id: "v3" });
    await view.rerender("v3");

    expect(api.getVersion).toHaveBeenLastCalledWith("v3");
    expect(view.holder.current.loading).toBe(true);
    expect(view.holder.current.report).toBeNull();
    expect(view.holder.current.version).toBeNull();

    // The old version's poll must not fire any more.
    const callsBefore = api.getReport.mock.calls.length;
    await tick(3000);
    expect(api.getReport.mock.calls.length).toBe(callsBefore);
    expect(api.getReport.mock.calls.every(([id], i) => i === 0 || id === "v3")).toBe(true);

    await act(async () => slow.resolve({ ...VERSION, id: "v3", number: 3 }));
    expect(view.holder.current.loading).toBe(false);
    expect(view.holder.current.report.version_id).toBe("v3");
    expect(view.holder.current.version.id).toBe("v3");
    await view.unmount();
  });

  it("ignores a late answer for a version the user has already left", async () => {
    const slowV2 = deferred();
    api.getVersion.mockReturnValueOnce(slowV2.promise);
    const view = mount("v2");
    await view.render();
    api.getVersion.mockResolvedValue({ ...VERSION, id: "v3", number: 3 });
    api.getReport.mockResolvedValue({ ...report({ narrative: NARRATIVE }), version_id: "v3" });
    await view.rerender("v3");

    await act(async () => slowV2.resolve(VERSION));

    expect(view.holder.current.version.id).toBe("v3");
    expect(view.holder.current.report.version_id).toBe("v3");
    await view.unmount();
  });

  it("stops polling and drops the data when disabled", async () => {
    api.getReport.mockResolvedValue(report());
    const view = mount("v2");
    await view.render();

    await view.rerender("v2", false);
    expect(view.holder.current).toEqual({ report: null, version: null, loading: false, error: null, gaveUp: false });
    const calls = api.getReport.mock.calls.length;
    await tick(30000);

    expect(api.getReport).toHaveBeenCalledTimes(calls);
    await view.unmount();
  });
});
