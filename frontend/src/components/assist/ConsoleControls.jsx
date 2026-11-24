import { Button } from "../ui/button";
import { DEFAULT_FILTERS, sourceBadgeText } from "./consoleFilters";

const RANGES = [
  { value: "24h", label: "24 hours" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "all", label: "All" },
];

const SOURCES = [
  { value: "all", label: "All" },
  { value: "live", label: "Live only" },
  { value: "demo", label: "Demo dataset" },
];

const selectClass =
  "h-8 rounded-md border border-border bg-background px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]";

// The controls every tab shares: time range, source, version and Refresh, plus the badge that says
// which data is on screen. `value` is `{ range, source, version }` (version is a number or null);
// every change calls `onChange` with the whole new object. `versions` are the numbers the version
// select offers. Times everywhere in the console are UTC.
export default function ConsoleControls({ value, onChange, versions = [], onRefresh, loading = false }) {
  const filters = { ...DEFAULT_FILTERS, ...value };
  // A version that is selected stays in the list even if the list has not arrived (or changed).
  const options = [...new Set([...versions, ...(filters.version != null ? [filters.version] : [])])].sort(
    (a, b) => a - b
  );

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-3 rounded-lg border border-border bg-white px-4 py-3">
      <span
        data-source-badge
        className="rounded-full border border-border bg-muted px-3 py-1 text-xs font-medium text-foreground"
      >
        {sourceBadgeText(filters.source)}
      </span>

      <div role="group" aria-label="Time range" className="inline-flex overflow-hidden rounded-md border border-border">
        {RANGES.map((r) => {
          const pressed = filters.range === r.value;
          return (
            <button
              key={r.value}
              type="button"
              aria-pressed={pressed}
              onClick={() => onChange({ ...filters, range: r.value })}
              className={`h-8 whitespace-nowrap px-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--ring)] ${
                pressed ? "bg-foreground text-background" : "bg-background text-foreground hover:bg-muted"
              }`}
            >
              {r.label}
            </button>
          );
        })}
      </div>

      <div className="flex items-center gap-2">
        <label htmlFor="console-source" className="text-xs text-muted-foreground">
          Source
        </label>
        <select
          id="console-source"
          className={selectClass}
          value={filters.source}
          onChange={(e) => onChange({ ...filters, source: e.target.value })}
        >
          {SOURCES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-2">
        <label htmlFor="console-version" className="text-xs text-muted-foreground">
          Version
        </label>
        <select
          id="console-version"
          className={selectClass}
          value={filters.version == null ? "" : String(filters.version)}
          onChange={(e) => onChange({ ...filters, version: e.target.value === "" ? null : Number(e.target.value) })}
        >
          <option value="">All</option>
          {options.map((n) => (
            <option key={n} value={String(n)}>
              v{n}
            </option>
          ))}
        </select>
      </div>

      <div className="ml-auto flex items-center gap-3">
        <span className="text-xs text-muted-foreground">All times are UTC.</span>
        <Button type="button" variant="outline" size="sm" onClick={onRefresh} disabled={loading}>
          Refresh data
        </Button>
      </div>
    </div>
  );
}
