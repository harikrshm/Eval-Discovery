import { lazy, Suspense, useMemo, useState } from "react";
import ConsoleBoundary from "./ConsoleBoundary";

// The console is its own chunk (it carries the charting library), loaded only when connected.
const loadConsole = () => import("./AssistConsole");

// Loads the console on demand. `load` returns the module promise (a parameter so tests can fail it).
// A rejected `lazy()` stays rejected for good, so Retry builds a new lazy component, which calls `load`
// again: `attempt` is part of the memo key. The boundary keeps a failed load from unmounting the app.
export default function ConsoleLoader({ load = loadConsole, ...consoleProps }) {
  const [attempt, setAttempt] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `attempt` is what makes a new lazy component
  const Console = useMemo(() => lazy(load), [load, attempt]);

  return (
    <ConsoleBoundary onRetry={() => setAttempt((n) => n + 1)} onDisconnect={consoleProps.onDisconnect}>
      <Suspense fallback={<p role="status" className="p-6 text-sm text-muted-foreground">Loading the console…</p>}>
        <Console {...consoleProps} />
      </Suspense>
    </ConsoleBoundary>
  );
}
