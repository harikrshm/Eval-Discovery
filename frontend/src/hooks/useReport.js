import { useState, useEffect } from "react";
import { getVersion, getReport } from "../api";
import { friendlyError } from "../lib/testStatus";

// The change report for one prompt version, plus the version itself (its text and its parent's
// text feed the diff). The numbers are ready as soon as the version resolves; the written
// explanation arrives later from a background job, so while the outcome exists but its narrative
// does not (and has not been given up on) the report is polled.

export const POLL_INTERVAL_MS = 3000;
export const POLL_LIMIT_MS = 120000;

const IDLE = { report: null, version: null, loading: false, error: null, gaveUp: false };

function awaitingNarrative(report) {
  const outcome = report?.outcome;
  return Boolean(outcome) && outcome.narrative == null && outcome.explanation_unavailable === false;
}

export function useReport(versionId, { enabled = true } = {}) {
  // `loaded` records which version it belongs to, so a stale answer is never shown for another.
  const [loaded, setLoaded] = useState({ versionId: null, report: null, version: null, error: null, gaveUp: false });

  const active = enabled && versionId != null;

  useEffect(() => {
    if (!active) return undefined;
    const controller = new AbortController();
    const { signal } = controller;
    let pollTimer = null;
    let limitTimer = null;
    let timedOut = false;
    let latest = null; // the newest report seen, to tell at the time limit whether a narrative is still missing

    // Later polls replace only the report: the version's text never changes.
    const publishReport = (report) => {
      latest = report;
      if (!signal.aborted) setLoaded((prev) => ({ ...prev, report }));
    };

    const poll = async () => {
      let report = null;
      try {
        report = await getReport(versionId);
      } catch {
        // A failed poll is not fatal: keep what is shown and try again until the time limit.
      }
      if (signal.aborted) return;
      if (report) {
        publishReport(report);
        if (!awaitingNarrative(report)) return;
      }
      if (!timedOut) pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    (async () => {
      try {
        // Only this first fetch counts as a view of the report; the polls below do not.
        const [version, report] = await Promise.all([getVersion(versionId), getReport(versionId, { view: true })]);
        if (signal.aborted) return;
        latest = report;
        setLoaded({ versionId, report, version, error: null, gaveUp: false });
        if (awaitingNarrative(report)) {
          limitTimer = setTimeout(() => {
            timedOut = true;
            clearTimeout(pollTimer);
            // Polling has stopped for good: tell the view the narrative is not coming.
            if (!signal.aborted && awaitingNarrative(latest)) setLoaded((prev) => ({ ...prev, gaveUp: true }));
          }, POLL_LIMIT_MS);
          pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (signal.aborted) return;
        setLoaded({ versionId, report: null, version: null, error: friendlyError(err), gaveUp: false });
      }
    })();

    return () => {
      controller.abort();
      clearTimeout(pollTimer);
      clearTimeout(limitTimer);
    };
  }, [active, versionId]);

  if (!active) return IDLE;
  if (loaded.versionId !== versionId) return { report: null, version: null, loading: true, error: null, gaveUp: false };
  return { report: loaded.report, version: loaded.version, loading: false, error: loaded.error, gaveUp: loaded.gaveUp };
}
