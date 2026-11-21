import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    getTests: vi.fn(),
    replayTest: vi.fn(),
    runAllTests: vi.fn(),
    buildTests: vi.fn(),
    suggestFix: vi.fn(),
    generateHeldout: vi.fn(),
    approveVersion: vi.fn(),
    rejectVersion: vi.fn(),
    restoreVersion: vi.fn(),
    getJob: vi.fn(),
  };
});

vi.mock("../lib/jobs", () => ({ pollJob: vi.fn() }));

import * as api from "../api";
import { pollJob } from "../lib/jobs";
import { useTests } from "./useTests";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function suite(overrides = {}) {
  return {
    prompt_version_id: "pv1",
    prompt_version_number: 1,
    stale_count: 0,
    criteria: [
      {
        criterion_id: "c1",
        status: "untested",
        judge_check: null,
        active_job_id: null,
        tests: [
          { id: "t1", role: "fix", source_run_id: "r1", baseline: null, current: null, active_job_id: null },
          { id: "t2", role: "heldout", source_run_id: "r2", baseline: null, current: null, active_job_id: null },
        ],
      },
      { criterion_id: "c2", status: "untested", judge_check: null, active_job_id: null, tests: [] },
    ],
    ...overrides,
  };
}

function withActive({ build = null, t1 = null, t2 = null } = {}) {
  const s = suite();
  s.criteria[0].tests[0].active_job_id = t1;
  s.criteria[0].tests[1].active_job_id = t2;
  s.criteria[1].active_job_id = build;
  return s;
}

// Resume fields the Phase 3 suite carries: suggest_job_id on a test, heldout_job_id on a criterion.
function withFixJobs({ suggest = null, heldout = null } = {}) {
  const s = suite();
  s.criteria[0].tests[0].suggest_job_id = suggest;
  s.criteria[0].heldout_job_id = heldout;
  return s;
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

function abortError() {
  const err = new Error("Aborted");
  err.name = "AbortError";
  return err;
}

// pollJob is replaced by a controllable fake: each call is recorded under its job id so a
// test can push updates and finish or fail the poll by hand.
const polls = {};
function installPollFake() {
  for (const key of Object.keys(polls)) delete polls[key];
  pollJob.mockImplementation(
    (_fetchJob, jobId, opts = {}) =>
      new Promise((resolve, reject) => {
        polls[jobId] = { opts, resolve, reject };
        opts.signal?.addEventListener("abort", () => reject(abortError()));
      }),
  );
}

function mount(initialAgentId) {
  const holder = { current: null };
  const record = (value) => {
    holder.current = value;
  };
  function Probe({ agentId, onValue }) {
    onValue(useTests(agentId));
    return null;
  }
  const container = document.createElement("div");
  const root = createRoot(container);
  const render = async (agentId) => {
    await act(async () => {
      root.render(createElement(Probe, { agentId, onValue: record }));
    });
  };
  return {
    holder,
    render: () => render(initialAgentId),
    rerender: render,
    unmount: () => act(() => root.unmount()),
  };
}

describe("useTests", () => {
  let errorSpy;

  beforeEach(() => {
    vi.clearAllMocks();
    installPollFake();
    api.getTests.mockResolvedValue(suite());
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("is idle with no agent", async () => {
    const view = mount(null);
    await view.render();

    expect(view.holder.current).toMatchObject({ suite: null, loading: false, error: null, jobs: {} });
    expect(api.getTests).not.toHaveBeenCalled();
    await view.unmount();
  });

  it("loads the suite for the agent", async () => {
    const view = mount("a1");
    await view.render();

    expect(api.getTests).toHaveBeenCalledWith("a1");
    expect(view.holder.current.loading).toBe(false);
    expect(view.holder.current.error).toBeNull();
    expect(view.holder.current.suite).toEqual(suite());
    expect(view.holder.current.jobs).toEqual({});
    expect(pollJob).not.toHaveBeenCalled();
    await view.unmount();
  });

  it("reports loading until the suite arrives", async () => {
    const pending = deferred();
    api.getTests.mockReturnValue(pending.promise);
    const view = mount("a1");
    await view.render();
    expect(view.holder.current.loading).toBe(true);
    expect(view.holder.current.suite).toBeNull();

    await act(async () => pending.resolve(suite()));

    expect(view.holder.current.loading).toBe(false);
    expect(view.holder.current.suite).not.toBeNull();
    await view.unmount();
  });

  it("surfaces a load error and lets refresh retry", async () => {
    api.getTests.mockRejectedValueOnce(new Error("Backend down"));
    const view = mount("a1");
    await view.render();

    expect(view.holder.current.error).toBe("Backend down");
    expect(view.holder.current.suite).toBeNull();
    expect(view.holder.current.loading).toBe(false);

    await act(async () => {
      await view.holder.current.refresh();
    });

    expect(view.holder.current.error).toBeNull();
    expect(view.holder.current.suite).toEqual(suite());
    await view.unmount();
  });

  it("keeps the last suite when a refresh fails", async () => {
    const view = mount("a1");
    await view.render();
    api.getTests.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await act(async () => {
      await view.holder.current.refresh();
    });

    expect(view.holder.current.suite).toEqual(suite());
    expect(view.holder.current.error).toBe("Failed to fetch");
    await view.unmount();
  });

  it("resumes polling an active job found in the suite and refreshes when it ends", async () => {
    api.getTests.mockResolvedValueOnce(withActive({ t1: "j1", build: "j2" }));
    const view = mount("a1");
    await view.render();

    expect(pollJob).toHaveBeenCalledTimes(2);
    expect(pollJob).toHaveBeenCalledWith(api.getJob, "j1", expect.objectContaining({ signal: expect.anything() }));
    expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ status: "queued" });
    expect(view.holder.current.jobs["build:c2"]).toMatchObject({ status: "queued" });

    await act(async () => {
      polls.j1.opts.onUpdate({ id: "j1", status: "running", result: null, error: null });
    });
    expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ status: "running" });

    expect(api.getTests).toHaveBeenCalledTimes(1);
    await act(async () => {
      polls.j1.resolve({ id: "j1", status: "done", result: {}, error: null });
    });

    expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ status: "done" });
    expect(api.getTests).toHaveBeenCalledTimes(2);
    expect(pollJob).toHaveBeenCalledTimes(2); // the refreshed suite has no active job: no new poll
    await view.unmount();
  });

  it("does not poll the same job twice when a refresh still shows it active", async () => {
    api.getTests.mockResolvedValue(withActive({ t1: "j1" }));
    const view = mount("a1");
    await view.render();

    await act(async () => {
      await view.holder.current.refresh();
    });

    expect(pollJob).toHaveBeenCalledTimes(1);
    await view.unmount();
  });

  describe("replay", () => {
    it("is pending at once, runs queued -> running -> done, then refetches", async () => {
      const accepted = deferred();
      api.replayTest.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();

      let call;
      await act(async () => {
        call = view.holder.current.replay("t1");
      });
      // The HTTP request has not returned yet.
      expect(pollJob).not.toHaveBeenCalled();
      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ status: "queued", error: null });

      await act(async () => {
        accepted.resolve({ job_id: "j7" });
        await call;
      });
      expect(api.replayTest).toHaveBeenCalledWith("t1");
      expect(pollJob).toHaveBeenCalledWith(api.getJob, "j7", expect.anything());
      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ status: "queued" });

      await act(async () => {
        polls.j7.opts.onUpdate({ id: "j7", status: "running", result: null, error: null });
      });
      expect(view.holder.current.jobs["replay:t1"].status).toBe("running");

      api.getTests.mockResolvedValueOnce(suite({ stale_count: 0, prompt_version_number: 2 }));
      await act(async () => {
        polls.j7.resolve({ id: "j7", status: "done", result: { verdict: "pass" }, error: null });
      });

      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ status: "done", result: { verdict: "pass" } });
      expect(api.getTests).toHaveBeenCalledTimes(2);
      expect(view.holder.current.suite.prompt_version_number).toBe(2);
      await view.unmount();
    });

    it("ignores a second click while the first is in flight", async () => {
      const accepted = deferred();
      api.replayTest.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();

      await act(async () => {
        view.holder.current.replay("t1");
        view.holder.current.replay("t1");
      });
      await act(async () => accepted.resolve({ job_id: "j7" }));
      await act(async () => {
        await view.holder.current.replay("t1");
      });

      expect(api.replayTest).toHaveBeenCalledTimes(1);
      await view.unmount();
    });

    it("marks the key failed with the message when the request is rejected", async () => {
      api.replayTest.mockRejectedValue(new api.ApiError("Test run limit reached for this hour. Try again later.", 429));
      const view = mount("a1");
      await view.render();

      await act(async () => {
        await view.holder.current.replay("t1");
      });

      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({
        status: "failed",
        error: "Test run limit reached for this hour. Try again later.",
      });
      expect(pollJob).not.toHaveBeenCalled();
      await view.unmount();
    });

    it("keeps the job error string when the job itself fails, and allows another try", async () => {
      api.replayTest.mockResolvedValue({ job_id: "j7" });
      const view = mount("a1");
      await view.render();
      await act(async () => {
        await view.holder.current.replay("t1");
      });

      await act(async () => {
        polls.j7.opts.onUpdate({ id: "j7", status: "failed", result: null, error: "The judge is unavailable." });
        polls.j7.resolve({ id: "j7", status: "failed", result: null, error: "The judge is unavailable." });
      });
      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({
        status: "failed",
        error: "The judge is unavailable.",
      });

      api.replayTest.mockResolvedValue({ job_id: "j8" });
      await act(async () => {
        await view.holder.current.replay("t1");
      });
      expect(api.replayTest).toHaveBeenCalledTimes(2);
      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ status: "queued", error: null });
      await view.unmount();
    });

    it("marks the key failed when polling gives up", async () => {
      api.replayTest.mockResolvedValue({ job_id: "j7" });
      const view = mount("a1");
      await view.render();
      await act(async () => {
        await view.holder.current.replay("t1");
      });

      await act(async () => {
        polls.j7.reject(new Error("Timed out waiting for the job."));
      });

      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({
        status: "failed",
        error: "Timed out waiting for the job.",
      });
      await view.unmount();
    });

    it("shows friendly copy instead of the raw browser message when polling cannot reach the server", async () => {
      api.replayTest.mockResolvedValue({ job_id: "j7" });
      const view = mount("a1");
      await view.render();
      await act(async () => {
        await view.holder.current.replay("t1");
      });

      await act(async () => {
        polls.j7.reject(new TypeError("Failed to fetch"));
      });

      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({
        status: "failed",
        error: "Couldn't reach the server. Check your connection, then try again.",
      });
      await view.unmount();
    });

    it("keeps a server ApiError message when polling gives up on it", async () => {
      api.replayTest.mockResolvedValue({ job_id: "j7" });
      const view = mount("a1");
      await view.render();
      await act(async () => {
        await view.holder.current.replay("t1");
      });

      await act(async () => {
        polls.j7.reject(new api.ApiError("Job not found.", 404));
      });

      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ error: "Job not found." });
      await view.unmount();
    });
  });

  describe("runAll", () => {
    it("marks every test without an active job pending at once, then follows the enqueued jobs", async () => {
      api.getTests.mockResolvedValueOnce(withActive({ t2: "jx" })); // t2 already running
      const view = mount("a1");
      await view.render();
      const accepted = deferred();
      api.runAllTests.mockReturnValue(accepted.promise);

      let call;
      await act(async () => {
        call = view.holder.current.runAll();
      });
      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ status: "queued" });
      expect(view.holder.current.jobs["replay:t2"]).toMatchObject({ status: "queued" }); // from resume
      expect(Object.keys(view.holder.current.jobs).sort()).toEqual(["replay:t1", "replay:t2"]);
      expect(pollJob).toHaveBeenCalledTimes(1); // only jx so far

      api.getTests.mockResolvedValueOnce(withActive({ t1: "j1", t2: "jx" }));
      await act(async () => {
        accepted.resolve({ job_ids: ["j1"] });
        await call;
      });

      expect(api.runAllTests).toHaveBeenCalledWith("a1");
      expect(pollJob).toHaveBeenCalledWith(api.getJob, "j1", expect.anything());
      expect(pollJob).toHaveBeenCalledTimes(2);
      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ status: "queued" });
      await view.unmount();
    });

    it("drops a pending key whose job is already finished in the reloaded suite", async () => {
      const view = mount("a1");
      await view.render();
      api.runAllTests.mockResolvedValue({ job_ids: ["j1", "j2"] });

      await act(async () => {
        await view.holder.current.runAll();
      });

      expect(view.holder.current.jobs).toEqual({});
      expect(pollJob).not.toHaveBeenCalled();
      await view.unmount();
    });

    it("does not leave a key pending when an earlier job for it already finished", async () => {
      api.replayTest.mockResolvedValue({ job_id: "j7" });
      const view = mount("a1");
      await view.render();
      await act(async () => {
        await view.holder.current.replay("t1");
      });
      await act(async () => {
        polls.j7.resolve({ id: "j7", status: "done", result: null, error: null });
      });
      api.runAllTests.mockResolvedValue({ job_ids: [] });

      await act(async () => {
        await view.holder.current.runAll();
      });

      expect(view.holder.current.jobs).toEqual({});
      await view.unmount();
    });

    it("marks each key it set pending as failed when the request is rejected", async () => {
      const view = mount("a1");
      await view.render();
      api.runAllTests.mockRejectedValue(new api.ApiError("Test run limit reached for this hour. Try again later.", 429));

      await act(async () => {
        await view.holder.current.runAll();
      });

      for (const key of ["replay:t1", "replay:t2"]) {
        expect(view.holder.current.jobs[key]).toMatchObject({
          status: "failed",
          error: "Test run limit reached for this hour. Try again later.",
        });
      }
      await view.unmount();
    });

    it("does nothing when there is no test to run", async () => {
      api.getTests.mockResolvedValue(suite({ criteria: [] }));
      const view = mount("a1");
      await view.render();

      await act(async () => {
        await view.holder.current.runAll();
      });

      expect(api.runAllTests).not.toHaveBeenCalled();
      await view.unmount();
    });
  });

  describe("buildTests", () => {
    it("is pending at once and keeps the finished job result", async () => {
      const accepted = deferred();
      api.buildTests.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();

      let call;
      await act(async () => {
        call = view.holder.current.buildTests("c2");
      });
      expect(view.holder.current.jobs["build:c2"]).toMatchObject({ status: "queued" });
      expect(pollJob).not.toHaveBeenCalled();

      await act(async () => {
        accepted.resolve({ job_id: "jb" });
        await call;
      });
      expect(api.buildTests).toHaveBeenCalledWith("c2");
      expect(pollJob).toHaveBeenCalledWith(api.getJob, "jb", expect.anything());

      await act(async () => {
        polls.jb.resolve({ id: "jb", status: "done", result: { already_handled: true }, error: null });
      });
      expect(view.holder.current.jobs["build:c2"]).toMatchObject({
        status: "done",
        result: { already_handled: true },
      });
      expect(api.getTests).toHaveBeenCalledTimes(2);
      await view.unmount();
    });

    it("marks the key failed when the request is rejected", async () => {
      api.buildTests.mockRejectedValue(new api.ApiError("Tests already exist for this criterion.", 409));
      const view = mount("a1");
      await view.render();

      await act(async () => {
        await view.holder.current.buildTests("c2");
      });

      expect(view.holder.current.jobs["build:c2"]).toMatchObject({
        status: "failed",
        error: "Tests already exist for this criterion.",
      });
      await view.unmount();
    });
  });

  describe("suggestFix", () => {
    it("is pending at once, follows the job, then reloads the suite", async () => {
      const accepted = deferred();
      api.suggestFix.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();

      let call;
      await act(async () => {
        call = view.holder.current.suggestFix("t1");
      });
      expect(pollJob).not.toHaveBeenCalled();
      expect(view.holder.current.jobs["suggest:t1"]).toMatchObject({ status: "queued", error: null });

      await act(async () => {
        accepted.resolve({ job_id: "js" });
        await call;
      });
      expect(api.suggestFix).toHaveBeenCalledWith("t1");
      expect(pollJob).toHaveBeenCalledWith(api.getJob, "js", expect.anything());

      await act(async () => {
        polls.js.resolve({ id: "js", status: "done", result: { version_id: "v2", number: 2 }, error: null });
      });
      expect(view.holder.current.jobs["suggest:t1"]).toMatchObject({
        status: "done",
        result: { version_id: "v2", number: 2 },
      });
      expect(api.getTests).toHaveBeenCalledTimes(2);
      await view.unmount();
    });

    it("ignores a second call while pending", async () => {
      const accepted = deferred();
      api.suggestFix.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();

      await act(async () => {
        view.holder.current.suggestFix("t1");
        view.holder.current.suggestFix("t1");
      });
      await act(async () => accepted.resolve({ job_id: "js" }));

      expect(api.suggestFix).toHaveBeenCalledTimes(1);
      await view.unmount();
    });

    it("marks the key failed with the server message when the request is rejected", async () => {
      api.suggestFix.mockRejectedValue(new api.ApiError("A fix is already proposed or being verified.", 409));
      const view = mount("a1");
      await view.render();

      await act(async () => {
        await view.holder.current.suggestFix("t1");
      });

      expect(view.holder.current.jobs["suggest:t1"]).toMatchObject({
        status: "failed",
        error: "A fix is already proposed or being verified.",
      });
      expect(pollJob).not.toHaveBeenCalled();
      await view.unmount();
    });

    it("turns a raw network failure into friendly copy", async () => {
      api.suggestFix.mockRejectedValue(new TypeError("Failed to fetch"));
      const view = mount("a1");
      await view.render();

      await act(async () => {
        await view.holder.current.suggestFix("t1");
      });

      expect(view.holder.current.jobs["suggest:t1"].error).toBe(
        "Couldn't reach the server. Check your connection, then try again.",
      );
      await view.unmount();
    });

    it("keeps the job's own error string when the job fails", async () => {
      api.suggestFix.mockResolvedValue({ job_id: "js" });
      const view = mount("a1");
      await view.render();
      await act(async () => {
        await view.holder.current.suggestFix("t1");
      });

      await act(async () => {
        polls.js.resolve({ id: "js", status: "failed", result: null, error: "Couldn't write a fix. Try again." });
      });

      expect(view.holder.current.jobs["suggest:t1"]).toMatchObject({
        status: "failed",
        error: "Couldn't write a fix. Try again.",
      });
      await view.unmount();
    });
  });

  describe("generateHeldout", () => {
    it("is pending at once and keeps the finished job result", async () => {
      const accepted = deferred();
      api.generateHeldout.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();

      let call;
      await act(async () => {
        call = view.holder.current.generateHeldout("c1");
      });
      expect(view.holder.current.jobs["heldout:c1"]).toMatchObject({ status: "queued" });
      expect(pollJob).not.toHaveBeenCalled();

      await act(async () => {
        accepted.resolve({ job_id: "jh" });
        await call;
      });
      expect(api.generateHeldout).toHaveBeenCalledWith("c1");

      await act(async () => {
        polls.jh.resolve({
          id: "jh",
          status: "done",
          result: { created: false, reason: "agent_handles_it" },
          error: null,
        });
      });
      expect(view.holder.current.jobs["heldout:c1"]).toMatchObject({
        status: "done",
        result: { created: false, reason: "agent_handles_it" },
      });
      expect(api.getTests).toHaveBeenCalledTimes(2);
      await view.unmount();
    });

    it("ignores a second call while pending", async () => {
      api.generateHeldout.mockReturnValue(deferred().promise);
      const view = mount("a1");
      await view.render();

      await act(async () => {
        view.holder.current.generateHeldout("c1");
        view.holder.current.generateHeldout("c1");
      });

      expect(api.generateHeldout).toHaveBeenCalledTimes(1);
      await view.unmount();
    });

    it("marks the key failed with the server message when the request is rejected", async () => {
      api.generateHeldout.mockRejectedValue(
        new api.ApiError("This criterion needs a fix test and no held-out test.", 409),
      );
      const view = mount("a1");
      await view.render();

      await act(async () => {
        await view.holder.current.generateHeldout("c1");
      });

      expect(view.holder.current.jobs["heldout:c1"]).toMatchObject({
        status: "failed",
        error: "This criterion needs a fix test and no held-out test.",
      });
      await view.unmount();
    });
  });

  describe("resuming fix-loop jobs", () => {
    it("resumes suggest_job_id and heldout_job_id from the suite", async () => {
      api.getTests.mockResolvedValueOnce(withFixJobs({ suggest: "js", heldout: "jh" }));
      const view = mount("a1");
      await view.render();

      expect(pollJob).toHaveBeenCalledWith(api.getJob, "js", expect.anything());
      expect(pollJob).toHaveBeenCalledWith(api.getJob, "jh", expect.anything());
      expect(view.holder.current.jobs["suggest:t1"]).toMatchObject({ status: "queued" });
      expect(view.holder.current.jobs["heldout:c1"]).toMatchObject({ status: "queued" });

      await act(async () => {
        polls.js.resolve({ id: "js", status: "done", result: {}, error: null });
      });
      expect(view.holder.current.jobs["suggest:t1"]).toMatchObject({ status: "done" });
      expect(api.getTests).toHaveBeenCalledTimes(2);
      await view.unmount();
    });

    it("does not poll a resumed fix-loop job twice", async () => {
      api.getTests.mockResolvedValue(withFixJobs({ suggest: "js" }));
      const view = mount("a1");
      await view.render();
      await act(async () => {
        await view.holder.current.refresh();
      });

      expect(pollJob).toHaveBeenCalledTimes(1);
      await view.unmount();
    });
  });

  describe("approve", () => {
    it("is pending at once, tracks the returned replay jobs, refreshes the suite and clears the pending key", async () => {
      const accepted = deferred();
      api.approveVersion.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();

      let call;
      await act(async () => {
        call = view.holder.current.approve("v2");
      });
      expect(view.holder.current.jobs["version:v2"]).toMatchObject({ status: "queued", error: null });

      await act(async () => {
        accepted.resolve({
          version_id: "v2",
          status: "approved_verifying",
          jobs: [
            { test_id: "t1", job_id: "jr1" },
            { test_id: "t2", job_id: "jr2" },
          ],
        });
        await call;
      });

      expect(api.approveVersion).toHaveBeenCalledWith("v2");
      expect(pollJob).toHaveBeenCalledWith(api.getJob, "jr1", expect.anything());
      expect(pollJob).toHaveBeenCalledWith(api.getJob, "jr2", expect.anything());
      expect(view.holder.current.jobs["replay:t1"]).toMatchObject({ status: "queued" });
      expect(view.holder.current.jobs["replay:t2"]).toMatchObject({ status: "queued" });
      expect(api.getTests).toHaveBeenCalledTimes(2);
      expect(view.holder.current.jobs["version:v2"]).toBeUndefined();
      await view.unmount();
    });

    it("does not follow a replay job twice when the refreshed suite lists it too", async () => {
      api.approveVersion.mockResolvedValue({
        version_id: "v2",
        status: "approved_verifying",
        jobs: [{ test_id: "t1", job_id: "jr1" }],
      });
      api.getTests.mockResolvedValueOnce(suite()).mockResolvedValue(withActive({ t1: "jr1" }));
      const view = mount("a1");
      await view.render();

      await act(async () => {
        await view.holder.current.approve("v2");
      });

      expect(pollJob).toHaveBeenCalledTimes(1);
      await view.unmount();
    });

    it("ignores a second call while the first is pending", async () => {
      api.approveVersion.mockReturnValue(deferred().promise);
      const view = mount("a1");
      await view.render();

      await act(async () => {
        view.holder.current.approve("v2");
        view.holder.current.approve("v2");
      });

      expect(api.approveVersion).toHaveBeenCalledTimes(1);
      await view.unmount();
    });

    it("surfaces the server message on failure under version:<id>, and allows another try", async () => {
      api.approveVersion.mockRejectedValue(new api.ApiError("The test queue is busy. Try again in a minute.", 429));
      const view = mount("a1");
      await view.render();

      await act(async () => {
        await view.holder.current.approve("v2");
      });

      expect(view.holder.current.jobs["version:v2"]).toMatchObject({
        status: "failed",
        error: "The test queue is busy. Try again in a minute.",
      });
      expect(api.getTests).toHaveBeenCalledTimes(1);

      api.approveVersion.mockResolvedValue({ version_id: "v2", status: "approved_verifying", jobs: [] });
      await act(async () => {
        await view.holder.current.approve("v2");
      });
      expect(api.approveVersion).toHaveBeenCalledTimes(2);
      expect(view.holder.current.jobs["version:v2"]).toBeUndefined();
      await view.unmount();
    });
  });

  describe.each([
    ["reject", "rejectVersion", { version_id: "v2", status: "rejected" }, "This fix can't be rejected in its current state."],
    ["restore", "restoreVersion", { id: "v1", number: 1, status: "restored" }, "This version can't be restored."],
  ])("%s", (action, apiName, okReply, serverMessage) => {
    it("is pending at once, refreshes the suite on success and clears the key", async () => {
      const accepted = deferred();
      api[apiName].mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();

      let call;
      await act(async () => {
        call = view.holder.current[action]("v2");
      });
      expect(view.holder.current.jobs["version:v2"]).toMatchObject({ status: "queued", error: null });
      expect(api.getTests).toHaveBeenCalledTimes(1);

      api.getTests.mockResolvedValueOnce(suite({ prompt_version_number: 3 }));
      await act(async () => {
        accepted.resolve(okReply);
        await call;
      });

      expect(api[apiName]).toHaveBeenCalledWith("v2");
      expect(api.getTests).toHaveBeenCalledTimes(2);
      expect(view.holder.current.suite.prompt_version_number).toBe(3);
      expect(view.holder.current.jobs["version:v2"]).toBeUndefined();
      expect(pollJob).not.toHaveBeenCalled();
      await view.unmount();
    });

    it("ignores a second call while pending", async () => {
      api[apiName].mockReturnValue(deferred().promise);
      const view = mount("a1");
      await view.render();

      await act(async () => {
        view.holder.current[action]("v2");
        view.holder.current[action]("v2");
      });

      expect(api[apiName]).toHaveBeenCalledTimes(1);
      await view.unmount();
    });

    it("surfaces the server message on failure and does not refresh", async () => {
      api[apiName].mockRejectedValue(new api.ApiError(serverMessage, 409));
      const view = mount("a1");
      await view.render();

      await act(async () => {
        await view.holder.current[action]("v2");
      });

      expect(view.holder.current.jobs["version:v2"]).toMatchObject({ status: "failed", error: serverMessage });
      expect(api.getTests).toHaveBeenCalledTimes(1);
      await view.unmount();
    });

    it("turns a raw network failure into friendly copy", async () => {
      api[apiName].mockRejectedValue(new TypeError("Failed to fetch"));
      const view = mount("a1");
      await view.render();

      await act(async () => {
        await view.holder.current[action]("v2");
      });

      expect(view.holder.current.jobs["version:v2"].error).toBe(
        "Couldn't reach the server. Check your connection, then try again.",
      );
      await view.unmount();
    });

    it("updates nothing after unmount", async () => {
      const accepted = deferred();
      api[apiName].mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();
      await act(async () => {
        view.holder.current[action]("v2");
      });

      await view.unmount();
      await act(async () => accepted.resolve(okReply));

      expect(api.getTests).toHaveBeenCalledTimes(1);
    });

    it("ignores a reply that lands after switching agents", async () => {
      const accepted = deferred();
      api[apiName].mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();
      await act(async () => {
        view.holder.current[action]("v2");
      });
      await view.rerender("a2");
      expect(api.getTests).toHaveBeenCalledTimes(2);

      await act(async () => accepted.resolve(okReply));

      expect(api.getTests).toHaveBeenCalledTimes(2);
      expect(view.holder.current.jobs).toEqual({});
      await view.unmount();
    });
  });

  describe("fix-loop unmount and agent switch", () => {
    it("ignores a suggestFix reply that lands after unmount", async () => {
      const accepted = deferred();
      api.suggestFix.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();
      await act(async () => {
        view.holder.current.suggestFix("t1");
      });

      await view.unmount();
      await act(async () => accepted.resolve({ job_id: "late" }));

      expect(pollJob).not.toHaveBeenCalled();
    });

    it("ignores a generateHeldout reply that lands after switching agents", async () => {
      const accepted = deferred();
      api.generateHeldout.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();
      await act(async () => {
        view.holder.current.generateHeldout("c1");
      });
      await view.rerender("a2");

      await act(async () => accepted.resolve({ job_id: "late" }));

      expect(pollJob).not.toHaveBeenCalled();
      expect(view.holder.current.jobs).toEqual({});
      await view.unmount();
    });

    it("tracks nothing from an approve reply that lands after unmount", async () => {
      const accepted = deferred();
      api.approveVersion.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();
      await act(async () => {
        view.holder.current.approve("v2");
      });

      await view.unmount();
      await act(async () =>
        accepted.resolve({ version_id: "v2", status: "approved_verifying", jobs: [{ test_id: "t1", job_id: "jr1" }] }),
      );

      expect(pollJob).not.toHaveBeenCalled();
      expect(api.getTests).toHaveBeenCalledTimes(1);
    });
  });

  describe("unmount and agent switch", () => {
    it("aborts polls and ignores a reply that arrives after unmount", async () => {
      api.getTests.mockResolvedValueOnce(withActive({ t1: "j1" }));
      const accepted = deferred();
      api.replayTest.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();
      await act(async () => {
        view.holder.current.replay("t2");
      });
      const signal = polls.j1.opts.signal;
      expect(signal.aborted).toBe(false);

      await view.unmount();
      expect(signal.aborted).toBe(true);

      await act(async () => accepted.resolve({ job_id: "late" }));
      expect(pollJob).toHaveBeenCalledTimes(1);
      expect(api.getTests).toHaveBeenCalledTimes(1);
    });

    it("does not refresh or update state when a poll finishes after unmount", async () => {
      api.getTests.mockResolvedValueOnce(withActive({ t1: "j1" }));
      const view = mount("a1");
      await view.render();
      const poll = polls.j1;

      await view.unmount();
      await act(async () => {
        poll.opts.onUpdate({ id: "j1", status: "running", result: null, error: null });
        poll.resolve({ id: "j1", status: "done", result: {}, error: null });
      });

      expect(api.getTests).toHaveBeenCalledTimes(1);
    });

    it("drops the old agent's jobs and polls, and ignores its late suite", async () => {
      api.getTests.mockResolvedValueOnce(withActive({ t1: "j1" }));
      const view = mount("a1");
      await view.render();
      const oldSignal = polls.j1.opts.signal;
      expect(view.holder.current.jobs["replay:t1"]).toBeDefined();

      const slow = deferred();
      api.getTests.mockReturnValueOnce(slow.promise);
      await view.rerender("a2");

      expect(oldSignal.aborted).toBe(true);
      expect(api.getTests).toHaveBeenLastCalledWith("a2");
      expect(view.holder.current.loading).toBe(true);
      expect(view.holder.current.suite).toBeNull();
      expect(view.holder.current.jobs).toEqual({});

      await act(async () => slow.resolve(suite({ prompt_version_number: 5 })));
      expect(view.holder.current.suite.prompt_version_number).toBe(5);
      expect(view.holder.current.loading).toBe(false);
      await view.unmount();
    });

    it("ignores a suite that resolves for an agent the user has already left", async () => {
      const slowA1 = deferred();
      api.getTests.mockReturnValueOnce(slowA1.promise);
      const view = mount("a1");
      await view.render();
      api.getTests.mockResolvedValueOnce(suite({ prompt_version_number: 9 }));
      await view.rerender("a2");

      await act(async () => slowA1.resolve(suite({ prompt_version_number: 1 })));

      expect(view.holder.current.suite.prompt_version_number).toBe(9);
      await view.unmount();
    });

    it("ignores a replay reply that lands after switching agents", async () => {
      const accepted = deferred();
      api.replayTest.mockReturnValue(accepted.promise);
      const view = mount("a1");
      await view.render();
      await act(async () => {
        view.holder.current.replay("t1");
      });
      await view.rerender("a2");

      await act(async () => accepted.resolve({ job_id: "late" }));

      expect(pollJob).not.toHaveBeenCalled();
      expect(view.holder.current.jobs).toEqual({});
      await view.unmount();
    });
  });
});
