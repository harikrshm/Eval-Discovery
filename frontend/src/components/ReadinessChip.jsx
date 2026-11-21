import { cn } from "../lib/utils";

// How many criteria with tests are passing. With `live` (the default) it is a status region that
// is always mounted, so a change of its text is announced; it renders empty while there is nothing
// to report. Two chips can be on screen at once (app header and panel); only one is live so the
// change is announced once, and the other is plain text.
export default function ReadinessChip({ readiness, live = true, className }) {
  let text = "";
  if (readiness) {
    const { passing, total, withoutTests, allPassing } = readiness;
    text = allPassing
      ? "All criteria passing. Testing is done for your saved tests."
      : `${passing} of ${total} criteria passing`;
    if (withoutTests > 0) text += ` · ${withoutTests} without tests`;
  }

  return (
    <span
      data-testid="readiness-chip"
      role={live ? "status" : undefined}
      className={cn(
        "inline-block rounded-md border border-border bg-muted px-2 py-0.5 text-xs text-foreground empty:hidden",
        className
      )}
    >
      {text}
    </span>
  );
}
