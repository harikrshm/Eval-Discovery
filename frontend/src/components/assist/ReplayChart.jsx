import BarsChart from "./charts/BarsChart";
import ChartCard from "./charts/ChartCard";

const SERIES = [
  { key: "before", label: "Passed before" },
  { key: "after", label: "Passed after" },
];
const COLUMNS = ["Version", "Passed before", "Passed after", "Tests checked"];

// Before/after replay results of the verified prompt versions, as grouped bars with a real table.
// `rows` come from `replayRow` (stored outcomes only): { label, before, after, total }, oldest first.
// A side with no result is a null: no bar and "n/a" in the table, never a zero. `width` fixes the
// chart size (tests).
export default function ReplayChart({ rows, width = null }) {
  return (
    <ChartCard
      title="Before and after replay"
      subtitle="How many of each fix's required tests passed before the fix and after it. Counts of stored replay results."
      tableColumns={COLUMNS}
      tableRows={rows.map((r) => [r.label, r.before, r.after, r.total])}
    >
      <BarsChart data={rows} categoryKey="label" series={SERIES} width={width} />
    </ChartCard>
  );
}
