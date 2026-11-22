import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, getVersions: vi.fn() };
});

import * as api from "../api";
import { useVersions } from "./useVersions";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const V1 = [{ id: "v1", number: 1, origin: "initial", status: "promoted", strength: null, created_at: "2026-11-20T12:00:00", is_current: true }];
const V2 = [
  { id: "v2", number: 2, origin: "manual", status: "promoted", strength: null, created_at: "2026-11-21T12:00:00", is_current: true },
  { ...V1[0], status: "superseded", is_current: false },
];

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function mount() {
  const holder = { current: null };
  const record = (value) => {
    holder.current = value;
  };
  function Probe({ agentId, refreshKey, enabled }) {
    record(useVersions(agentId, { refreshKey, enabled }));
    return null;
  }
  const container = document.createElement("div");
  const root = createRoot(container);
  return {
    holder,
    render: (agentId, refreshKey = "k1", enabled = true) =>
      act(async () => {
        root.render(createElement(Probe, { agentId, refreshKey, enabled }));
      }),
    unmount: () => act(() => root.unmount()),
  };
}

describe("useVersions", () => {
  let errorSpy;

  beforeEach(() => {
    vi.clearAllMocks();
    api.getVersions.mockResolvedValue(V1);
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("loads the agent's versions", async () => {
    const d = deferred();
    api.getVersions.mockReturnValue(d.promise);
    const view = mount();
    await view.render("a1");
    expect(api.getVersions).toHaveBeenCalledWith("a1");
    expect(view.holder.current.loading).toBe(true);
    expect(view.holder.current.versions).toBeNull();

    await act(async () => d.resolve(V1));
    expect(view.holder.current.loading).toBe(false);
    expect(view.holder.current.versions).toEqual(V1);
    expect(view.holder.current.error).toBeNull();
    await view.unmount();
  });

  it("does not load without an agent or while disabled", async () => {
    const view = mount();
    await view.render(null);
    await view.render("a1", "k1", false);
    expect(api.getVersions).not.toHaveBeenCalled();
    expect(view.holder.current.loading).toBe(false);
    await view.unmount();
  });

  it("loads again when the refresh key changes and when it becomes enabled", async () => {
    const view = mount();
    await view.render("a1", "k1", false);
    await view.render("a1", "k1", true);
    expect(api.getVersions).toHaveBeenCalledTimes(1);
    await view.render("a1", "k2", true);
    expect(api.getVersions).toHaveBeenCalledTimes(2);
    await view.unmount();
  });

  it("the newest load wins over an older, slower one", async () => {
    const slow = deferred();
    const fast = deferred();
    api.getVersions.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);
    const view = mount();
    await view.render("a1", "k1");
    await view.render("a1", "k2");

    await act(async () => fast.resolve(V2));
    await act(async () => slow.resolve(V1));
    expect(view.holder.current.versions).toEqual(V2);
    await view.unmount();
  });

  it("reload() fetches again and the newest answer wins", async () => {
    const view = mount();
    await view.render("a1");
    api.getVersions.mockResolvedValue(V2);
    await act(async () => {
      await view.holder.current.reload();
    });
    expect(view.holder.current.versions).toEqual(V2);
    await view.unmount();
  });

  it("never shows another agent's versions, and drops the old agent's late answer", async () => {
    const late = deferred();
    api.getVersions.mockReturnValueOnce(late.promise);
    const view = mount();
    await view.render("a1");
    api.getVersions.mockResolvedValue(V2);
    await view.render("a2");
    expect(view.holder.current.versions).toEqual(V2);

    await act(async () => late.resolve(V1));
    expect(view.holder.current.versions).toEqual(V2);
    await view.unmount();
  });

  it("keeps the last list and reports a plain error when a reload fails", async () => {
    const view = mount();
    await view.render("a1");
    api.getVersions.mockRejectedValue(new Error("Backend down"));
    await act(async () => {
      await view.holder.current.reload();
    });
    expect(view.holder.current.versions).toEqual(V1);
    expect(view.holder.current.error).toBe("Backend down");
    expect(view.holder.current.loading).toBe(false);

    api.getVersions.mockResolvedValue(V2);
    await act(async () => {
      await view.holder.current.reload();
    });
    expect(view.holder.current.error).toBeNull();
    await view.unmount();
  });

  it("does not touch state after unmount", async () => {
    const d = deferred();
    api.getVersions.mockReturnValue(d.promise);
    const view = mount();
    await view.render("a1");
    await view.unmount();
    await act(async () => d.resolve(V1));
    // The afterEach asserts that nothing was logged.
  });
});
