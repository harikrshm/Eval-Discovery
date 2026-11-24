// The legend is plain text beside a swatch, so a series is never identified by colour alone and
// the words are always in the page. `shape` is "line", "bar" or "marker" (dashed).
function Swatch({ color, shape }) {
  if (shape === "bar") {
    return <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: color }} />;
  }
  return (
    <svg aria-hidden="true" width="18" height="8" className="shrink-0">
      <line
        x1="1"
        y1="4"
        x2="17"
        y2="4"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={shape === "marker" ? "3 3" : undefined}
      />
    </svg>
  );
}

export default function ChartLegend({ items }) {
  if (!items.length) return null;
  return (
    <ul data-legend className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {items.map((item) => (
        <li key={`${item.shape}-${item.label}`} className="flex items-center gap-1.5">
          <Swatch color={item.color} shape={item.shape} />
          <span>{item.label}</span>
        </li>
      ))}
    </ul>
  );
}
