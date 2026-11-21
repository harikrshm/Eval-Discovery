import { ApiError } from "../api";

const MAX_TRANSIENT_FAILURES = 2;

function abortError() {
  const err = new Error("Polling was cancelled.");
  err.name = "AbortError";
  return err;
}

function isFinished(job) {
  return job.status === "done" || job.status === "failed";
}

// A bad token or a job that does not exist will not fix itself.
function isFatal(err) {
  return err instanceof ApiError && (err.status === 401 || err.status === 404);
}

// Polls fetchJob(jobId) until the job is done or failed. A failed job resolves (it is a
// result, not an error); only trouble in polling itself rejects. Any other error is
// tolerated twice in a row (a network blip), then rejects with the last one.
export function pollJob(
  fetchJob,
  jobId,
  { intervalMs = 2000, timeoutMs = 180000, signal, onUpdate } = {},
) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let failures = 0;
    let pollTimer = null;
    let deadline = null;

    function finish(settle, value) {
      if (settled) return;
      settled = true;
      clearTimeout(pollTimer);
      clearTimeout(deadline);
      if (signal) signal.removeEventListener("abort", onAbort);
      settle(value);
    }

    function onAbort() {
      finish(reject, abortError());
    }

    async function poll() {
      let job;
      try {
        job = await fetchJob(jobId);
      } catch (err) {
        if (settled) return;
        failures += 1;
        if (isFatal(err) || failures > MAX_TRANSIENT_FAILURES) {
          finish(reject, err);
        } else {
          pollTimer = setTimeout(poll, intervalMs);
        }
        return;
      }
      if (settled) return;
      failures = 0;
      if (onUpdate) onUpdate(job);
      if (isFinished(job)) {
        finish(resolve, job);
      } else {
        pollTimer = setTimeout(poll, intervalMs);
      }
    }

    if (signal) {
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort);
    }
    deadline = setTimeout(
      () => finish(reject, new Error("Timed out waiting for the job.")),
      timeoutMs,
    );
    poll();
  });
}
