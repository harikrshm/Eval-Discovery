import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  getTests,
  getJob,
  replayTest as apiReplayTest,
  runAllTests as apiRunAllTests,
  buildTests as apiBuildTests,
  suggestFix as apiSuggestFix,
  generateHeldout as apiGenerateHeldout,
  approveVersion as apiApproveVersion,
  rejectVersion as apiRejectVersion,
  restoreVersion as apiRestoreVersion,
} from "../api";
import { pollJob } from "../lib/jobs";
import { friendlyError } from "../lib/testStatus";

// The server owns the suite; this hook mirrors it and follows background jobs.
// `jobs` maps "replay:<testId>" / "build:<criterionId>" / "suggest:<testId>" /
// "heldout:<criterionId>" to { status, error, result }; "version:<versionId>" does the same for
// approve / reject / restore, which are not jobs but follow the same pending-then-failed rule.
// A key goes to "queued" the moment an action starts (before the request returns) so the
// UI can disable its button at once, and a failed job keeps its error message.

const EMPTY_JOBS = {};
const IN_FLIGHT = ["queued", "running"];

function errorMessage(err) {
  return err instanceof Error && err.message ? err.message : "Something went wrong.";
}

function replayKey(testId) {
  return `replay:${testId}`;
}

function buildKey(criterionId) {
  return `build:${criterionId}`;
}

function suggestKey(testId) {
  return `suggest:${testId}`;
}

function heldoutKey(criterionId) {
  return `heldout:${criterionId}`;
}

function versionKey(versionId) {
  return `version:${versionId}`;
}

export function useTests(agentId) {
  // `loaded` and `jobState` record which agent they belong to, so a stale or
  // not-yet-finished load is never mistaken for the current agent.
  const [loaded, setLoaded] = useState({ agentId: null, suite: null, error: null });
  const [jobState, setJobState] = useState({ agentId: null, map: EMPTY_JOBS });

  // Everything async works against the context of the agent it started for; once that
  // agent is left (or the hook unmounts) its context is aborted and every callback bails.
  const ctxRef = useRef(null);

  // The helpers below call each other (loading resumes jobs, a finished job reloads), so
  // they are built together once; they touch only refs and state setters.
  const engine = useMemo(() => {
    const isLive = (ctx) => ctxRef.current === ctx && !ctx.controller.signal.aborted;

    function patchJob(ctx, key, value) {
      if (!isLive(ctx)) return;
      ctx.jobs = { ...ctx.jobs, [key]: value };
      setJobState({ agentId: ctx.agentId, map: ctx.jobs });
    }

    function dropJob(ctx, key) {
      if (!isLive(ctx) || !(key in ctx.jobs)) return;
      const next = { ...ctx.jobs };
      delete next[key];
      ctx.jobs = next;
      setJobState({ agentId: ctx.agentId, map: ctx.jobs });
    }

    // Loads the suite and resumes any job it lists as active. Overlapping loads resolve
    // to the newest one's answer, so an older, slower response never overwrites it.
    function loadSuite(ctx) {
      const run = (async () => {
        try {
          const suite = await getTests(ctx.agentId);
          if (!isLive(ctx)) return null;
          if (ctx.latestLoad !== run) return ctx.latestLoad;
          ctx.suite = suite;
          setLoaded({ agentId: ctx.agentId, suite, error: null });
          for (const criterion of suite.criteria) {
            if (criterion.active_job_id) {
              track(ctx, buildKey(criterion.criterion_id), criterion.active_job_id);
            }
            if (criterion.heldout_job_id) {
              track(ctx, heldoutKey(criterion.criterion_id), criterion.heldout_job_id);
            }
            for (const test of criterion.tests) {
              if (test.active_job_id) track(ctx, replayKey(test.id), test.active_job_id);
              if (test.suggest_job_id) track(ctx, suggestKey(test.id), test.suggest_job_id);
            }
          }
          return suite;
        } catch (err) {
          if (!isLive(ctx)) return null;
          if (ctx.latestLoad !== run) return ctx.latestLoad;
          // Keep whatever suite was last loaded; the error says the view may be out of date.
          setLoaded({ agentId: ctx.agentId, suite: ctx.suite, error: errorMessage(err) });
          return null;
        }
      })();
      ctx.latestLoad = run;
      return run;
    }

    // Follows a job to its end, then reloads the suite. A job id already followed (or
    // already finished) is ignored, so a reload that still lists it is harmless.
    function track(ctx, key, jobId) {
      if (!isLive(ctx) || ctx.tracked.get(key) === jobId) return;
      ctx.tracked.set(key, jobId);
      patchJob(ctx, key, { status: "queued", error: null, result: null });

      const applyJob = (job) => {
        if (ctx.tracked.get(key) !== jobId) return;
        patchJob(ctx, key, {
          status: job.status,
          error: job.error ?? null,
          result: job.result ?? null,
        });
      };

      pollJob(getJob, jobId, { signal: ctx.controller.signal, onUpdate: applyJob }).then(
        (job) => {
          if (!isLive(ctx)) return;
          applyJob(job);
          loadSuite(ctx);
        },
        (err) => {
          if (!isLive(ctx)) return;
          // Polling gave up, but the job may still be running: forget it so a later
          // reload can pick it up again.
          ctx.tracked.delete(key);
          patchJob(ctx, key, { status: "failed", error: friendlyError(err), result: null });
        }
      );
    }

    return { isLive, patchJob, dropJob, loadSuite, track };
  }, []);

  useEffect(() => {
    if (agentId == null) return undefined;
    const ctx = {
      agentId,
      controller: new AbortController(),
      suite: null,
      jobs: EMPTY_JOBS,
      tracked: new Map(), // key -> id of the job being followed (or last followed)
      latestLoad: null,
    };
    ctxRef.current = ctx;
    engine.loadSuite(ctx);
    return () => {
      ctx.controller.abort();
      if (ctxRef.current === ctx) ctxRef.current = null;
    };
  }, [agentId, engine]);

  const ready = agentId != null && loaded.agentId === agentId;
  const loading = agentId != null && !ready;

  const refresh = useCallback(async () => {
    const ctx = ctxRef.current;
    if (!ctx) return;
    await engine.loadSuite(ctx);
  }, [engine]);

  // Starts one job-creating request. The key is pending from the call (a second click is
  // ignored); a rejected request turns it into a failed job carrying the message.
  const start = useCallback(
    async (key, request) => {
      const ctx = ctxRef.current;
      if (!ctx) return;
      if (IN_FLIGHT.includes(ctx.jobs[key]?.status)) return;
      engine.patchJob(ctx, key, { status: "queued", error: null, result: null });
      try {
        const { job_id: jobId } = await request();
        engine.track(ctx, key, jobId);
      } catch (err) {
        engine.patchJob(ctx, key, { status: "failed", error: friendlyError(err), result: null });
      }
    },
    [engine]
  );

  const replay = useCallback((testId) => start(replayKey(testId), () => apiReplayTest(testId)), [start]);

  const buildTests = useCallback(
    (criterionId) => start(buildKey(criterionId), () => apiBuildTests(criterionId)),
    [start]
  );

  const suggestFix = useCallback((testId) => start(suggestKey(testId), () => apiSuggestFix(testId)), [start]);

  const generateHeldout = useCallback(
    (criterionId) => start(heldoutKey(criterionId), () => apiGenerateHeldout(criterionId)),
    [start]
  );

  // Approve, reject and restore change the version state, not a job. The key is pending from
  // the call until the suite has been reloaded (so the button cannot be clicked again while the
  // view still shows the old state); a rejected request leaves a failed key with the message.
  const versionAction = useCallback(
    async (versionId, request, onReply) => {
      const ctx = ctxRef.current;
      if (!ctx) return;
      const key = versionKey(versionId);
      if (IN_FLIGHT.includes(ctx.jobs[key]?.status)) return;
      engine.patchJob(ctx, key, { status: "queued", error: null, result: null });
      let reply;
      try {
        reply = await request();
      } catch (err) {
        engine.patchJob(ctx, key, { status: "failed", error: friendlyError(err), result: null });
        return;
      }
      if (!engine.isLive(ctx)) return;
      if (onReply) onReply(ctx, reply);
      await engine.loadSuite(ctx);
      engine.dropJob(ctx, key);
    },
    [engine]
  );

  // The reply lists the replay jobs that now verify the fix, one per test.
  const approve = useCallback(
    (versionId) =>
      versionAction(versionId, () => apiApproveVersion(versionId), (ctx, reply) => {
        for (const { test_id: testId, job_id: jobId } of reply.jobs ?? []) {
          engine.track(ctx, replayKey(testId), jobId);
        }
      }),
    [versionAction, engine]
  );

  const reject = useCallback((versionId) => versionAction(versionId, () => apiRejectVersion(versionId)), [versionAction]);

  const restore = useCallback(
    (versionId) => versionAction(versionId, () => apiRestoreVersion(versionId)),
    [versionAction]
  );

  // The response only lists job ids, so the suite is reloaded to learn which test each
  // belongs to. A key still following the same job as before the reload got no new job
  // (it finished before the reload), so it stops being pending.
  const runAll = useCallback(async () => {
    const ctx = ctxRef.current;
    if (!ctx || !ctx.suite) return;
    const keys = ctx.suite.criteria
      .flatMap((criterion) => criterion.tests)
      .filter((test) => !test.active_job_id && !IN_FLIGHT.includes(ctx.jobs[replayKey(test.id)]?.status))
      .map((test) => replayKey(test.id));
    if (keys.length === 0) return;
    const followedBefore = new Map(keys.map((key) => [key, ctx.tracked.get(key)]));

    for (const key of keys) engine.patchJob(ctx, key, { status: "queued", error: null, result: null });
    try {
      await apiRunAllTests(ctx.agentId);
    } catch (err) {
      for (const key of keys) {
        engine.patchJob(ctx, key, { status: "failed", error: friendlyError(err), result: null });
      }
      return;
    }
    await engine.loadSuite(ctx);
    for (const key of keys) {
      if (ctx.tracked.get(key) === followedBefore.get(key)) engine.dropJob(ctx, key);
    }
  }, [engine]);

  return {
    suite: ready ? loaded.suite : null,
    loading,
    error: ready ? loaded.error : null,
    jobs: jobState.agentId === agentId ? jobState.map : EMPTY_JOBS,
    refresh,
    replay,
    runAll,
    buildTests,
    suggestFix,
    generateHeldout,
    approve,
    reject,
    restore,
  };
}
