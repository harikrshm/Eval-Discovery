import { cn } from "../lib/utils";

// How many criteria with tests are passing. With `live` (the default) it is a status region that
// is always mounted, so a change of its text is announced; it renders empty while there is nothing
// to report. Two chips can be on screen at once (app header and panel); only one is live so the
// change is announced once, and the other is plain text.
export default function ReadinessChip({ readiness, live = true, className }) {
  let text = "";
  if (readiness) {
    const { passing, total, withoutTests, allPassing } = readiness;
    if (total === 0 && withoutTests > 0) {
      text = "No criteria have tests yet"; // "0 of 0 passing" would read as a result
    } else {
      text = allPassing
        ? "All criteria passing. Testing is done for your saved tests."
        : `${passing} of ${total} criteria passing`;
      if (withoutTests > 0) text += ` · ${withoutTests} without tests`;
    }
  }

  return (
    <span
      data-testid="readiness-chip"
      role={live ? "status" : undefined}
      className={
        // Empty: visually hidden but still in the accessibility tree (display:none would drop the
        // live region, and its first text might then go unannounced); no space, padding or margin.
        text
          ? cn("inline-block rounded-md border border-border bg-muted px-2 py-0.5 text-xs text-foreground", className)
          : "sr-only"
      }
    >
      {text}
    </span>
  );
}
