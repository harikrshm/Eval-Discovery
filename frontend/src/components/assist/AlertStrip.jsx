import { AlertTriangle, OctagonAlert } from "lucide-react";
import { STATUS } from "./charts/palette";

// The alerts that are active now. Each is a button: the shell switches to the alert's tab and scrolls
// to its anchor. Severity is a word and an icon shape (never colour alone); an alert computed from
// the demo dataset carries a "demo" tag. Nothing is drawn when there are no alerts.
export default function AlertStrip({ alerts, onOpen }) {
  if (!Array.isArray(alerts) || alerts.length === 0) return null;
  return (
    <ul data-alert-strip aria-label="Active alerts" className="space-y-2">
      {alerts.map((alert) => {
        const critical = alert.severity === "critical";
        const Icon = critical ? OctagonAlert : AlertTriangle;
        return (
          <li key={alert.id}>
            <button
              type="button"
              onClick={() => onOpen?.(alert)}
              className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border bg-card px-4 py-3 text-left text-sm transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
            >
              <span className="flex items-center gap-1.5 font-medium text-foreground">
                <Icon
                  aria-hidden="true"
                  className="h-4 w-4 shrink-0"
                  style={{ color: critical ? STATUS.bad : STATUS.neutral }}
                />
                {critical ? "Critical" : "Warning"}
              </span>
              <span className="font-medium text-foreground">{alert.title}</span>
              {alert.detail ? <span className="text-muted-foreground">{alert.detail}</span> : null}
              {alert.demo ? (
                <span
                  data-demo-tag
                  className="rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-foreground"
                >
                  demo
                </span>
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
