import { useState, useEffect, useCallback, useRef } from "react";
import { getVersions } from "../api";
import { friendlyError } from "../lib/testStatus";

// The agent's prompt versions, newest first. The list is loaded while `enabled` and again whenever
// `refreshKey` changes (the caller derives it from what can change the list); `reload()` loads it
// on demand. Overlapping loads resolve to the newest one's answer, and an answer that arrives
// after the agent changed, loading was switched off or the component unmounted is dropped.
// A failed load keeps the last list and reports a message beside it.
export function useVersions(agentId, { refreshKey = null, enabled = true } = {}) {
  // `loaded` records which agent it belongs to, so another agent's list is never shown.
  const [loaded, setLoaded] = useState({ agentId: null, versions: null, error: null });
  const ctxRef = useRef(null);

  const load = useCallback(() => {
    const ctx = ctxRef.current;
    if (!ctx) return Promise.resolve();
    const seq = ++ctx.seq;
    const current = () => ctxRef.current === ctx && !ctx.controller.signal.aborted && ctx.seq === seq;
    return getVersions(ctx.agentId).then(
      (versions) => {
        if (!current()) return;
        setLoaded({ agentId: ctx.agentId, versions, error: null });
      },
      (err) => {
        if (!current()) return;
        setLoaded((prev) => ({
          agentId: ctx.agentId,
          versions: prev.agentId === ctx.agentId ? prev.versions : null,
          error: friendlyError(err),
        }));
      }
    );
  }, []);

  useEffect(() => {
    if (agentId == null || !enabled) return undefined;
    const ctx = { agentId, controller: new AbortController(), seq: 0 };
    ctxRef.current = ctx;
    load();
    return () => {
      ctx.controller.abort();
      if (ctxRef.current === ctx) ctxRef.current = null;
    };
  }, [agentId, enabled, refreshKey, load]);

  const active = agentId != null && enabled;
  const ready = loaded.agentId === agentId;
  return {
    versions: ready ? loaded.versions : null,
    // Only the first load of a list is "loading"; a reload refreshes it quietly.
    loading: active && !ready,
    error: ready ? loaded.error : null,
    reload: load,
  };
}
