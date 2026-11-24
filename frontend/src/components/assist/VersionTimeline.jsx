import { Badge } from "../ui/badge";
import { utcStamp } from "./format";

// Every field comes from the server's own version rows (numbers, fixed origin and status words and
// timestamps), never from model text. Same words as the version history in the criteria panel.
const ORIGIN_LABELS = {
  initial: "initial",
  manual: "edited by you",
  suggested: "suggested fix",
  restored: "restored",
};

const STATUS_LABELS = {
  failed_verification: "failed verification",
  rejected: "rejected",
  proposed: "proposed",
  approved_verifying: "verifying",
  superseded: "earlier version",
};

// A suggested version with no verification record was never approved (a manual edit replaced it
// while it was only proposed).
function statusLabel(version) {
  const finished = ["promoted", "superseded", "failed_verification"].includes(version.status);
  if (version.origin === "suggested" && !version.strength && finished) return "never approved";
  if (version.status === "promoted") return version.is_current ? "live" : "earlier version";
  return STATUS_LABELS[version.status] ?? version.status;
}

// The prompt versions of the live agent, newest first, with the current one marked. Live rows only:
// a demo dataset never creates a prompt version, so nothing here is labelled demo.
export default function VersionTimeline({ versions }) {
  if (!Array.isArray(versions)) return null;
  const ordered = [...versions].sort((a, b) => b.number - a.number);
  return (
    <ol className="space-y-2">
      {ordered.map((version) => (
        <li
          key={version.id}
          aria-current={version.is_current ? "true" : undefined}
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm"
        >
          <p className="min-w-0 break-words text-foreground">
            <span data-version-number className="font-medium">
              v{version.number}
            </span>
            <span className="text-muted-foreground">
              {" "}
              · {ORIGIN_LABELS[version.origin] ?? version.origin} · {statusLabel(version)} · {utcStamp(version.created_at)}
            </span>
          </p>
          {version.is_current ? (
            <Badge data-current variant="outline" className="border-green-200 bg-green-50 text-green-800">
              Current
            </Badge>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
