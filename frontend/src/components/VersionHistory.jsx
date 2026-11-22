import { useState, useEffect, useId, useRef } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { useVersions } from "../hooks/useVersions";

// Every field shown here comes from the server's own version rows (numbers, fixed status and origin
// words, timestamps), never from model text; it is still rendered only as plain text.

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

const STRENGTH_LABELS = {
  full: "held-out verified",
  weaker: "verified without a held-out test",
};

const RESTORABLE = ["promoted", "superseded", "failed_verification"];
const VERIFYING_TEXT = "A fix is being verified. Wait for it to finish.";
const DEFAULT_RESTORE_ERROR = "That didn't work. Try again.";

function statusLabel(version, isCurrent) {
  if (version.status === "promoted") return isCurrent ? "live" : "earlier version";
  return STATUS_LABELS[version.status] ?? version.status;
}

// Datetimes are naive UTC strings; without a zone the browser would read them as local time.
function formatDate(value) {
  if (typeof value !== "string" || !value) return null;
  const date = new Date(/(Z|[+-]\d\d:?\d\d)$/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

function summary(version, isCurrent) {
  return [
    `v${version.number}`,
    ORIGIN_LABELS[version.origin] ?? version.origin,
    statusLabel(version, isCurrent),
    STRENGTH_LABELS[version.strength] ?? null,
    formatDate(version.created_at),
  ]
    .filter(Boolean)
    .join(" · ");
}

// The collapsible list of prompt versions, newest first, with a confirmed Restore on the ones that
// can be restored. `onRestore(id)` resolves false when the restore did not go through; its message
// arrives as `error`. `busyId` is a restore in flight (every Restore waits for it) and `verifying`
// is true while a suggested fix verifies (the server refuses a restore then).
export default function VersionHistory({
  versions,
  currentId,
  verifying = false,
  onRestore,
  busyId = null,
  error = null,
  loading = false,
  loadError = null,
  onRetry,
  onOpenChange,
}) {
  const [open, setOpen] = useState(false);
  const [confirmId, setConfirmId] = useState(null);
  const [localBusyId, setLocalBusyId] = useState(null);
  const [announcement, setAnnouncement] = useState("");
  const uid = useId();
  const bodyId = `${uid}-body`;
  const noteId = `${uid}-note`;
  const sectionRef = useRef(null);
  const toggleRef = useRef(null);
  const restoreRefs = useRef(new Map());
  const confirmRef = useRef(null);
  const pendingFocus = useRef(null); // { kind: "restore", id } | { kind: "confirm" }
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Runs after each render: focus lands on a control that exists by now (the one the user acted on
  // may have just been replaced).
  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    pendingFocus.current = null;
    if (target.kind === "confirm") confirmRef.current?.focus();
    else restoreRefs.current.get(target.id)?.focus();
  });

  const busy = busyId ?? localBusyId;
  const locked = busy != null || verifying;
  const ordered = Array.isArray(versions) ? [...versions].sort((a, b) => b.number - a.number) : null;
  const anyRestorable = Boolean(ordered?.some((v) => RESTORABLE.includes(v.status) && v.id !== currentId));

  function toggleOpen() {
    const next = !open;
    setOpen(next);
    if (!next) setConfirmId(null);
    onOpenChange?.(next);
  }

  function ask(id) {
    if (locked) return;
    setConfirmId(id);
    pendingFocus.current = { kind: "confirm" };
  }

  function cancel(id) {
    if (localBusyId != null) return;
    setConfirmId(null);
    pendingFocus.current = { kind: "restore", id };
  }

  // Focus is only moved back when it is still inside this section (or lost): a user who has gone
  // elsewhere while the restore ran is not pulled back.
  function focusIsHere() {
    const active = document.activeElement;
    return !active || active === document.body || Boolean(sectionRef.current?.contains(active));
  }

  async function confirm(version) {
    if (locked) return;
    setLocalBusyId(version.id);
    setAnnouncement("");
    // The confirm button is disabled from this click, which would drop its focus.
    toggleRef.current?.focus();
    let ok = false;
    try {
      ok = (await onRestore?.(version.id)) !== false;
    } catch {
      ok = false;
    }
    if (!mounted.current) return;
    setLocalBusyId(null);
    setConfirmId(null);
    if (ok) {
      setAnnouncement(`Restored v${version.number} as a new version. Chat uses it now.`);
      if (focusIsHere()) toggleRef.current?.focus();
    } else if (focusIsHere()) {
      pendingFocus.current = { kind: "restore", id: version.id };
    }
  }

  function onConfirmKeyDown(e, id) {
    if (e.key !== "Escape" || localBusyId != null) return;
    e.stopPropagation();
    cancel(id);
  }

  return (
    <section ref={sectionRef} aria-label="Version history" className="rounded-lg border border-border bg-white">
      <button
        ref={toggleRef}
        type="button"
        onClick={toggleOpen}
        aria-expanded={open}
        aria-controls={bodyId}
        className="w-full flex items-center justify-between gap-2 px-4 py-3 text-left text-sm font-medium text-foreground hover:bg-muted"
      >
        <span>Version history</span>
        {open ? (
          <ChevronUp className="h-4 w-4 flex-shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : (
          <ChevronDown className="h-4 w-4 flex-shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
      </button>

      {/* Always rendered so the announcement of a restore is picked up when its text appears. */}
      <p role="status" className="sr-only">
        {announcement}
      </p>

      {open && (
        <div id={bodyId} className="space-y-2 border-t border-border px-4 pb-4 pt-3">
          {loadError && (
            <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-red-700">
              <p>Couldn&apos;t load the version history. {loadError}</p>
              <Button type="button" variant="outline" size="sm" onClick={() => onRetry?.()}>
                Retry
              </Button>
            </div>
          )}

          {!ordered && loading && <p className="text-sm text-muted-foreground">Loading versions…</p>}
          {ordered && ordered.length === 0 && <p className="text-sm text-muted-foreground">No versions yet.</p>}

          {verifying && anyRestorable && (
            <p id={noteId} className="text-xs text-muted-foreground">
              {VERIFYING_TEXT}
            </p>
          )}

          {error && (
            <div role="alert" className="text-sm text-red-700">
              {error}
            </div>
          )}

          {ordered && ordered.length > 0 && (
            <ul className="space-y-2">
              {ordered.map((version) => {
                const isCurrent = version.id === currentId;
                const canRestore = RESTORABLE.includes(version.status) && !isCurrent;
                const confirming = canRestore && confirmId === version.id;
                const promptId = `${uid}-confirm-${version.id}`;
                return (
                  <li key={version.id} className="rounded-md border border-border px-3 py-2">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className="min-w-0 break-words text-sm text-foreground">{summary(version, isCurrent)}</p>
                      {isCurrent && (
                        <Badge variant="outline" className="border-green-200 bg-green-50 text-green-800">
                          Current
                        </Badge>
                      )}
                    </div>

                    {canRestore && !confirming && (
                      <Button
                        ref={(el) => {
                          if (el) restoreRefs.current.set(version.id, el);
                          else restoreRefs.current.delete(version.id);
                        }}
                        type="button"
                        variant="outline"
                        size="sm"
                        className="mt-2"
                        disabled={locked}
                        aria-describedby={verifying ? noteId : undefined}
                        onClick={() => ask(version.id)}
                      >
                        Restore
                      </Button>
                    )}

                    {confirming && (
                      <div
                        role="group"
                        aria-labelledby={promptId}
                        className="mt-2 space-y-2"
                        onKeyDown={(e) => onConfirmKeyDown(e, version.id)}
                      >
                        <p id={promptId} className="text-sm text-foreground">
                          Restore v{version.number}? Chat will use it right away.
                        </p>
                        <div className="flex items-center gap-2">
                          <Button
                            ref={confirmRef}
                            type="button"
                            variant="accent"
                            size="sm"
                            disabled={locked}
                            aria-busy={localBusyId === version.id}
                            onClick={() => confirm(version)}
                          >
                            Confirm restore
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            disabled={localBusyId != null}
                            onClick={() => cancel(version.id)}
                          >
                            Cancel
                          </Button>
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function isActive(job) {
  return job?.status === "queued" || job?.status === "running";
}

// Loads the list (only while the section is open) and wires the restore from useTests. The list
// reloads when the live version or the candidate's status changes, and after a restore.
export function VersionHistoryContainer({ agentId, suite, jobs, onRestore }) {
  const [open, setOpen] = useState(false);
  const [restoringId, setRestoringId] = useState(null);
  const candidateStatus = suite?.candidate?.status ?? "";
  const liveId = suite?.prompt_version_id ?? "";
  const { versions, loading, error, reload } = useVersions(agentId, {
    refreshKey: `${liveId}:${candidateStatus}`,
    enabled: open,
  });

  // Only this section's own restore is reported here; an approve or reject error on a version
  // belongs to the change review.
  const job = restoringId ? jobs?.[`version:${restoringId}`] : null;
  const currentId = liveId || versions?.find((v) => v.is_current)?.id || null;

  async function handleRestore(versionId) {
    setRestoringId(versionId);
    const ok = await onRestore(versionId);
    if (ok !== false) reload();
    return ok;
  }

  return (
    <VersionHistory
      versions={versions}
      currentId={currentId}
      verifying={candidateStatus === "approved_verifying"}
      onRestore={handleRestore}
      busyId={isActive(job) ? restoringId : null}
      error={job?.status === "failed" ? job.error || DEFAULT_RESTORE_ERROR : null}
      loading={loading}
      loadError={error}
      onRetry={reload}
      onOpenChange={setOpen}
    />
  );
}
