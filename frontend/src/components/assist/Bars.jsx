import { NA, isNumber, pct } from "./format";

// One labelled horizontal bar made of plain elements: no chart library. The value is always also
// text next to the bar. A missing value (`null`) shows "n/a" and no bar at all.
//
// `value` is a share from 0 to 1; `detail` is extra text shown before the percentage.
export function BarRow({ label, value, detail = null }) {
  const known = isNumber(value);
  const text = known ? pct(value) : NA;
  const spoken = detail && known ? `${detail}, ${text}` : text;
  const width = known ? Math.min(100, Math.max(0, value * 100)) : 0;

  return (
    <li className="grid grid-cols-[minmax(6rem,10rem)_1fr_auto] items-center gap-3 text-sm">
      <span className="min-w-0 break-words text-foreground">{label}</span>
      {known ? (
        <span
          role="img"
          aria-label={`${label}: ${spoken}`}
          className="block h-3 w-full overflow-hidden rounded bg-muted"
        >
          <span data-bar-fill className="block h-full bg-foreground/70" style={{ width: `${width}%` }} />
        </span>
      ) : (
        <span role="img" aria-label={`${label}: ${NA}`} className="text-muted-foreground">
          {NA}
        </span>
      )}
      {known ? (
        <span aria-hidden="true" className="whitespace-nowrap text-right tabular-nums text-muted-foreground">
          {detail ? `${detail} · ${text}` : text}
        </span>
      ) : (
        <span aria-hidden="true" />
      )}
    </li>
  );
}

export function BarList({ items, label }) {
  return (
    <ul aria-label={label} className="space-y-2">
      {items.map((item) => (
        <BarRow key={item.key} label={item.label} value={item.value} detail={item.detail} />
      ))}
    </ul>
  );
}
