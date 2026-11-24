import { useId, useState } from "react";
import { Button } from "../../ui/button";
import { NA, isNumber } from "../format";

function cell(value) {
  if (value === null || value === undefined) return NA;
  if (typeof value === "number" && !isNumber(value)) return NA;
  return String(value);
}

// "Quality: 3 rows from 10:00 to 12:00. Latest 12:00: Grade 4.4, Rate 55%."
// The first column names the row (a time or a category); the rest are the values.
function summarise(title, columns, rows) {
  if (!rows.length) return `${title}: no data.`;
  const first = cell(rows[0][0]);
  const last = rows[rows.length - 1];
  const latest = columns
    .slice(1)
    .map((name, i) => `${name} ${cell(last[i + 1])}`)
    .join(", ");
  const count = `${rows.length} ${rows.length === 1 ? "row" : "rows"}`;
  const range = rows.length > 1 ? ` from ${first} to ${cell(last[0])}` : "";
  return `${title}: ${count}${range}. Latest ${cell(last[0])}${latest ? `: ${latest}` : ""}.`;
}

// A titled card around one chart. The chart is described for screen readers and a real <table>
// with the same numbers sits behind a toggle (the text equivalent). `interactive` is for charts
// whose marks are focusable buttons: role="img" would hide them from assistive tech, so the
// wrapper becomes a labelled group instead. WARNING: ALWAYS pass `interactive` when the chart
// inside has `onSelect` (a BarsChart with clickable bars); without it the bar buttons are hidden
// from screen readers. Nothing enforces this at runtime.
export default function ChartCard({
  title,
  subtitle = null,
  tableColumns,
  tableRows,
  children,
  id,
  interactive = false,
}) {
  const generated = useId();
  const base = id ?? generated;
  const headingId = `${base}-title`;
  const tableId = `${base}-table`;
  const [showTable, setShowTable] = useState(false);

  return (
    <section aria-labelledby={headingId} id={id} className="rounded-lg border border-border bg-card p-4 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 id={headingId} className="text-sm font-semibold text-foreground">
            {title}
          </h3>
          {subtitle ? <p className="text-xs text-muted-foreground">{subtitle}</p> : null}
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-expanded={showTable}
          aria-controls={tableId}
          onClick={() => setShowTable((open) => !open)}
        >
          {showTable ? "Hide table" : "View as table"}
        </Button>
      </div>

      <div
        role={interactive ? "group" : "img"}
        aria-label={summarise(title, tableColumns, tableRows)}
        className="min-w-0"
      >
        {children}
      </div>

      <div id={tableId}>
        {showTable ? (
          <div className="max-h-72 overflow-auto">
            <table className="w-full text-left text-xs tabular-nums">
              <caption className="pb-2 text-left text-xs text-muted-foreground">{title}</caption>
              <thead>
                <tr className="border-b border-border">
                  {tableColumns.map((name) => (
                    <th key={name} scope="col" className="py-1 pr-3 font-medium text-foreground">
                      {name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tableRows.map((row, r) => (
                  <tr key={r} className="border-b border-border/60 last:border-0">
                    {row.map((value, c) =>
                      c === 0 ? (
                        <th key={c} scope="row" className="py-1 pr-3 font-normal text-foreground">
                          {cell(value)}
                        </th>
                      ) : (
                        <td key={c} className="py-1 pr-3 text-muted-foreground">
                          {cell(value)}
                        </td>
                      ),
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </section>
  );
}
