import { useCallback, useEffect, useRef, useState } from "react";

// A list with no rows is empty; any other payload is ready unless the caller says otherwise.
function defaultIsEmpty(data) {
  return data == null || (Array.isArray(data) && data.length === 0);
}

function messageOf(err) {
  return err instanceof Error && err.message ? err.message : "Something went wrong.";
}

// Runs `fetcher(filters)` whenever the filters, `reloadKey` or a retry change, for the console tabs.
//
//   const { state, data, error, refreshing, retry } = useConsoleQuery(assistOverview, filters, reloadKey, { isEmpty });
//
// state: "loading" (nothing to show yet) | "ready" | "empty" (`isEmpty(data)`) | "error".
// A response that arrives after the filters moved on is ignored, never shown. While a refresh is in
// flight the last good `data` stays visible (state stays ready/empty, `refreshing` is true); when a
// refresh fails, state is "error" and `data` still holds the last good payload. `filters` is read by
// value (range, source, version), so a new object with the same values does not refetch, and
// `fetcher` is always the latest one passed in.
export function useConsoleQuery(fetcher, filters, reloadKey = 0, { isEmpty = defaultIsEmpty } = {}) {
  const { range, source, version = null } = filters ?? {};
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState({ key: null, data: null, error: null });
  const fetcherRef = useRef(fetcher);
  const key = `${range}|${source}|${version}|${reloadKey}|${attempt}`;

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    let stale = false;
    (async () => fetcherRef.current({ range, source, version }))()
      .then((data) => {
        if (!stale) setResult({ key, data, error: null });
      })
      .catch((err) => {
        if (!stale) setResult((prev) => ({ key, data: prev.data, error: messageOf(err) }));
      });
    return () => {
      stale = true;
    };
  }, [key, range, source, version]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const settled = result.key === key;
  const hasData = result.data !== null;
  let state;
  if (!settled) state = hasData ? (isEmpty(result.data) ? "empty" : "ready") : "loading";
  else if (result.error) state = "error";
  else state = isEmpty(result.data) ? "empty" : "ready";

  return {
    state,
    data: result.data,
    error: settled ? result.error : null,
    refreshing: !settled && hasData,
    retry,
  };
}
