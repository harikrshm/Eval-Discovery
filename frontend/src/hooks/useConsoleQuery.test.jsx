import { afterEach, describe, expect, it, vi } from "vitest";
import { act, createElement, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { useConsoleQuery } from "./useConsoleQuery";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let container;
let latest;

function Probe({ fetcher, filters, reloadKey, options, onResult }) {
  const result = useConsoleQuery(fetcher, filters, reloadKey, options);
  useEffect(() => {
    onResult(result);
  });
  return null;
}

async function render(props) {
  if (!root) {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  }
  await act(async () => {
    root.render(createElement(Probe, { ...props, onResult: (r) => (latest = r) }));
  });
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container?.remove();
  root = null;
  container = null;
  latest = null;
});

const F = { range: "30d", source: "all", version: null };

describe("useConsoleQuery", () => {
  it("is loading, then ready with the data", async () => {
    const d = deferred();
    const fetcher = vi.fn(() => d.promise);
    await render({ fetcher, filters: F, reloadKey: 0 });
    expect(latest.state).toBe("loading");
    expect(latest.data).toBeNull();
    expect(fetcher).toHaveBeenCalledWith(F);
    await act(async () => d.resolve({ n: 1 }));
    expect(latest.state).toBe("ready");
    expect(latest.data).toEqual({ n: 1 });
    expect(latest.error).toBeNull();
  });

  it("reports empty when the payload is empty by the given rule", async () => {
    const fetcher = vi.fn(async () => ({ n: 0 }));
    await render({ fetcher, filters: F, reloadKey: 0, options: { isEmpty: (d) => d.n === 0 } });
    expect(latest.state).toBe("empty");
    const list = vi.fn(async () => []);
    await render({ fetcher: list, filters: F, reloadKey: 1 });
    expect(latest.state).toBe("empty");
  });

  it("ignores a stale response that arrives after a filter change", async () => {
    const first = deferred();
    const second = deferred();
    const fetcher = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    await render({ fetcher, filters: F, reloadKey: 0 });
    await render({ fetcher, filters: { ...F, range: "7d" }, reloadKey: 0 });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1][0].range).toBe("7d");

    await act(async () => second.resolve({ from: "7d" }));
    expect(latest.data).toEqual({ from: "7d" });
    await act(async () => first.resolve({ from: "30d" }));
    expect(latest.data).toEqual({ from: "7d" });
    expect(latest.state).toBe("ready");
  });

  it("ignores a stale failure too", async () => {
    const first = deferred();
    const second = deferred();
    const fetcher = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    await render({ fetcher, filters: F, reloadKey: 0 });
    await render({ fetcher, filters: { ...F, source: "live" }, reloadKey: 0 });
    await act(async () => second.resolve({ ok: true }));
    await act(async () => first.reject(new Error("old failure")));
    expect(latest.state).toBe("ready");
    expect(latest.error).toBeNull();
  });

  it("does not refetch when only the identity of the filters object changes", async () => {
    const fetcher = vi.fn(async () => ({ n: 1 }));
    await render({ fetcher, filters: { ...F }, reloadKey: 0 });
    await render({ fetcher, filters: { ...F }, reloadKey: 0 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("keeps the last good data visible while a refresh is in flight", async () => {
    const next = deferred();
    const fetcher = vi.fn().mockResolvedValueOnce({ n: 1 }).mockReturnValueOnce(next.promise);
    await render({ fetcher, filters: F, reloadKey: 0 });
    expect(latest.data).toEqual({ n: 1 });
    await render({ fetcher, filters: F, reloadKey: 1 });
    expect(latest.state).toBe("ready");
    expect(latest.data).toEqual({ n: 1 });
    expect(latest.refreshing).toBe(true);
    await act(async () => next.resolve({ n: 2 }));
    expect(latest.data).toEqual({ n: 2 });
    expect(latest.refreshing).toBe(false);
  });

  it("refetches on a quiet key change without reporting refreshing, keeping the data and the state", async () => {
    const next = deferred();
    const fetcher = vi.fn().mockResolvedValueOnce({ n: 1 }).mockReturnValueOnce(next.promise);
    await render({ fetcher, filters: F, reloadKey: 0, options: { quietKey: "a" } });
    await render({ fetcher, filters: F, reloadKey: 0, options: { quietKey: "b" } });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(latest.state).toBe("ready");
    expect(latest.data).toEqual({ n: 1 });
    expect(latest.refreshing).toBe(false);
    await act(async () => next.resolve({ n: 2 }));
    expect(latest.data).toEqual({ n: 2 });
    expect(latest.refreshing).toBe(false);
  });

  it("reports refreshing when an explicit reload arrives while a quiet refetch is in flight", async () => {
    const quiet = deferred();
    const explicit = deferred();
    const fetcher = vi.fn().mockResolvedValueOnce({ n: 1 }).mockReturnValueOnce(quiet.promise).mockReturnValueOnce(explicit.promise);
    await render({ fetcher, filters: F, reloadKey: 0, options: { quietKey: "a" } });
    await render({ fetcher, filters: F, reloadKey: 0, options: { quietKey: "b" } });
    expect(latest.refreshing).toBe(false);
    await render({ fetcher, filters: F, reloadKey: 1, options: { quietKey: "b" } });
    expect(latest.refreshing).toBe(true);
    await act(async () => explicit.resolve({ n: 3 }));
    expect(latest.refreshing).toBe(false);
  });

  it("ignores a stale response after a quiet key change", async () => {
    const first = deferred();
    const second = deferred();
    const fetcher = vi.fn().mockResolvedValueOnce({ n: 0 }).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    await render({ fetcher, filters: F, reloadKey: 0, options: { quietKey: "a" } });
    await render({ fetcher, filters: F, reloadKey: 0, options: { quietKey: "b" } });
    await render({ fetcher, filters: F, reloadKey: 0, options: { quietKey: "c" } });
    await act(async () => second.resolve({ n: 2 }));
    await act(async () => first.resolve({ n: 1 }));
    expect(latest.data).toEqual({ n: 2 });
  });

  it("exposes the error and retry fetches again", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("Boom")).mockResolvedValueOnce({ n: 3 });
    await render({ fetcher, filters: F, reloadKey: 0 });
    expect(latest.state).toBe("error");
    expect(latest.error).toBe("Boom");
    expect(latest.data).toBeNull();
    await act(async () => latest.retry());
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(latest.state).toBe("ready");
    expect(latest.data).toEqual({ n: 3 });
    expect(latest.error).toBeNull();
  });

  it("keeps the previous data next to the error when a refresh fails", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ n: 1 }).mockRejectedValueOnce(new Error("Later failure"));
    await render({ fetcher, filters: F, reloadKey: 0 });
    await render({ fetcher, filters: F, reloadKey: 1 });
    expect(latest.state).toBe("error");
    expect(latest.error).toBe("Later failure");
    expect(latest.data).toEqual({ n: 1 });
  });

  it("falls back to a generic message for an error with no text", async () => {
    const fetcher = vi.fn().mockRejectedValue("weird");
    await render({ fetcher, filters: F, reloadKey: 0 });
    expect(latest.error).toBe("Something went wrong.");
  });

  it("stops listening after unmount", async () => {
    const d = deferred();
    const fetcher = vi.fn(() => d.promise);
    await render({ fetcher, filters: F, reloadKey: 0 });
    await act(async () => root.unmount());
    root = null;
    await act(async () => d.resolve({ n: 1 }));
    expect(latest.state).toBe("loading");
  });
});
