import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { pollJob } from "./jobs";

const job = (status, extra = {}) => ({ id: "j1", type: "replay", status, result: null, error: null, ...extra });

describe("pollJob", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls through queued and running until done", async () => {
    const fetchJob = vi
      .fn()
      .mockResolvedValueOnce(job("queued"))
      .mockResolvedValueOnce(job("running"))
      .mockResolvedValueOnce(job("done", { result: { tests_created: 2 } }));
    const onUpdate = vi.fn();

    const promise = pollJob(fetchJob, "j1", { intervalMs: 1000, onUpdate });
    await vi.advanceTimersByTimeAsync(2000);

    await expect(promise).resolves.toMatchObject({ status: "done", result: { tests_created: 2 } });
    expect(fetchJob).toHaveBeenCalledTimes(3);
    expect(fetchJob).toHaveBeenCalledWith("j1");
    expect(onUpdate.mock.calls.map(([j]) => j.status)).toEqual(["queued", "running", "done"]);
  });

  it("resolves (does not reject) with a failed job", async () => {
    const fetchJob = vi.fn().mockResolvedValue(job("failed", { error: "Judge down." }));

    const out = await pollJob(fetchJob, "j1", { intervalMs: 1000 });

    expect(out).toMatchObject({ status: "failed", error: "Judge down." });
    expect(fetchJob).toHaveBeenCalledTimes(1);
  });

  it("stops polling once the job is finished", async () => {
    const fetchJob = vi.fn().mockResolvedValue(job("done"));
    await pollJob(fetchJob, "j1", { intervalMs: 1000 });

    await vi.advanceTimersByTimeAsync(10000);

    expect(fetchJob).toHaveBeenCalledTimes(1);
  });

  it("rejects with a timeout error when the job never finishes", async () => {
    const fetchJob = vi.fn().mockResolvedValue(job("running"));

    const settled = pollJob(fetchJob, "j1", { intervalMs: 1000, timeoutMs: 5000 }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(5000);

    const err = await settled;
    expect(err).toBeInstanceOf(Error);
    expect(err.message).toBe("Timed out waiting for the job.");
    const calls = fetchJob.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);
    expect(fetchJob).toHaveBeenCalledTimes(calls);
  });

  it("rejects with an AbortError and stops polling when the signal aborts", async () => {
    const fetchJob = vi.fn().mockResolvedValue(job("running"));
    const controller = new AbortController();

    const settled = pollJob(fetchJob, "j1", { intervalMs: 1000, signal: controller.signal }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(1500);
    controller.abort();

    const err = await settled;
    expect(err.name).toBe("AbortError");
    const calls = fetchJob.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);
    expect(fetchJob).toHaveBeenCalledTimes(calls);
  });

  it("rejects at once with an AbortError when the signal is already aborted", async () => {
    const fetchJob = vi.fn();
    const controller = new AbortController();
    controller.abort();

    const err = await pollJob(fetchJob, "j1", { signal: controller.signal }).catch((e) => e);

    expect(err.name).toBe("AbortError");
    expect(fetchJob).not.toHaveBeenCalled();
  });

  it("does not report an update that arrives after an abort", async () => {
    let resolveFetch;
    const fetchJob = vi.fn().mockReturnValue(new Promise((r) => (resolveFetch = r)));
    const onUpdate = vi.fn();
    const controller = new AbortController();

    const settled = pollJob(fetchJob, "j1", { signal: controller.signal, onUpdate }).catch((e) => e);
    controller.abort();
    resolveFetch(job("done"));

    expect((await settled).name).toBe("AbortError");
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it.each([404, 401])("rejects immediately on an ApiError %i", async (status) => {
    const fetchJob = vi.fn().mockRejectedValue(new ApiError("Job not found.", status));

    const err = await pollJob(fetchJob, "j1", { intervalMs: 1000 }).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(status);
    expect(fetchJob).toHaveBeenCalledTimes(1);
  });

  it("tolerates two consecutive transient failures", async () => {
    const fetchJob = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(job("done"));

    const promise = pollJob(fetchJob, "j1", { intervalMs: 1000 });
    await vi.advanceTimersByTimeAsync(2000);

    await expect(promise).resolves.toMatchObject({ status: "done" });
  });

  it("rejects on the third consecutive transient failure", async () => {
    const boom = new TypeError("Failed to fetch");
    const fetchJob = vi.fn().mockRejectedValue(boom);

    const settled = pollJob(fetchJob, "j1", { intervalMs: 1000 }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(2000);

    expect(await settled).toBe(boom);
    expect(fetchJob).toHaveBeenCalledTimes(3);
  });

  it("resets the failure count after a successful poll", async () => {
    const boom = new TypeError("Failed to fetch");
    const fetchJob = vi
      .fn()
      .mockRejectedValueOnce(boom)
      .mockRejectedValueOnce(boom)
      .mockResolvedValueOnce(job("running"))
      .mockRejectedValueOnce(boom)
      .mockRejectedValueOnce(boom)
      .mockResolvedValueOnce(job("done"));

    const promise = pollJob(fetchJob, "j1", { intervalMs: 1000 });
    await vi.advanceTimersByTimeAsync(5000);

    await expect(promise).resolves.toMatchObject({ status: "done" });
  });

  describe("timeouts", () => {
    const sequence = (statuses) => {
      // statuses: [[fromMs, status]] ordered; the fake clock decides which one applies.
      return vi.fn(async () => {
        const now = Date.now() - start;
        let current = statuses[0][1];
        for (const [from, status] of statuses) if (now >= from) current = status;
        return job(current);
      });
    };
    let start;
    beforeEach(() => {
      start = Date.now();
    });

    it("does not time out a job that sat queued for 10 minutes before running and finishing", async () => {
      const fetchJob = sequence([
        [0, "queued"],
        [10 * 60 * 1000, "running"],
        [10 * 60 * 1000 + 60 * 1000, "done"],
      ]);
      const settled = pollJob(fetchJob, "j1", { intervalMs: 2000 }).then(
        (j) => j,
        (e) => e,
      );
      await vi.advanceTimersByTimeAsync(11 * 60 * 1000 + 2000);
      expect(await settled).toMatchObject({ status: "done" });
    });

    it("times out after the running limit while the job is running", async () => {
      const fetchJob = vi.fn().mockResolvedValue(job("running"));
      let done = false;
      const settled = pollJob(fetchJob, "j1", { intervalMs: 2000 })
        .catch((e) => e)
        .finally(() => (done = true));
      await vi.advanceTimersByTimeAsync(179000);
      expect(done).toBe(false);
      await vi.advanceTimersByTimeAsync(2000);
      const err = await settled;
      expect(err.message).toBe("Timed out waiting for the job.");
    });

    it("restarts the running clock when the status changes", async () => {
      // queued for 100 s, then running for 150 s (250 s in total, past 180 s), then done.
      const fetchJob = sequence([
        [0, "queued"],
        [100000, "running"],
        [250000, "done"],
      ]);
      const settled = pollJob(fetchJob, "j1", { intervalMs: 2000 }).then(
        (j) => j,
        (e) => e,
      );
      await vi.advanceTimersByTimeAsync(252000);
      expect(await settled).toMatchObject({ status: "done" });
    });

    it("times out a job queued beyond the queued limit", async () => {
      const fetchJob = vi.fn().mockResolvedValue(job("queued"));
      let done = false;
      const settled = pollJob(fetchJob, "j1", { intervalMs: 2000 })
        .catch((e) => e)
        .finally(() => (done = true));
      await vi.advanceTimersByTimeAsync(899000);
      expect(done).toBe(false);
      await vi.advanceTimersByTimeAsync(2000);
      const err = await settled;
      expect(err.message).toBe("Timed out waiting for the job.");
    });

    it("honours custom timeoutMs and queuedTimeoutMs", async () => {
      const fetchJob = vi.fn().mockResolvedValue(job("queued"));
      let done = false;
      const settled = pollJob(fetchJob, "j1", {
        intervalMs: 1000,
        timeoutMs: 3000,
        queuedTimeoutMs: 8000,
      })
        .catch((e) => e)
        .finally(() => (done = true));
      await vi.advanceTimersByTimeAsync(7000);
      expect(done).toBe(false);
      await vi.advanceTimersByTimeAsync(1500);
      expect((await settled).message).toBe("Timed out waiting for the job.");
    });
  });
});
