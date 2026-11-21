import { useState, useEffect, useId } from "react";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import { Label } from "./ui/label";
import { Separator } from "./ui/separator";
import { ScrollArea } from "./ui/scroll-area";
import { ChevronDown, ChevronUp, Plus, X } from "lucide-react";
import PromptEditor from "./PromptEditor";
import TestsSection from "./TestsSection";
import { ChangeReviewContainer } from "./ChangeReview";
import { staleMessage } from "../lib/testStatus";

const TEXT_FIELDS = [
  { key: "definition", label: "Definition" },
  { key: "good_example", label: "Good example" },
  { key: "bad_example", label: "Bad example" },
];

function CriterionCard({
  criterion,
  index,
  isExpanded,
  onToggle,
  onUpdate,
  criterionSuite,
  jobs,
  agentName,
  userRoleLabel,
  onReplay,
  onBuildTests,
}) {
  const uid = useId();
  const bodyId = `${uid}-body`;

  function handleBlur(field, value) {
    onUpdate(criterion.id, { [field]: value });
  }

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-white">
      {/* The whole row toggles the card; the full name wraps instead of truncating */}
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isExpanded}
        aria-controls={bodyId}
        className="w-full flex items-start gap-2 px-4 py-3 text-left hover:bg-muted"
      >
        <span className="text-sm font-medium text-muted-foreground flex-shrink-0">
          #{index + 1}
        </span>
        <span className="flex-1 min-w-0 text-sm font-medium text-foreground break-words">
          {criterion.name}
        </span>
        {isExpanded ? (
          <ChevronUp className="h-4 w-4 mt-0.5 flex-shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : (
          <ChevronDown className="h-4 w-4 mt-0.5 flex-shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
      </button>

      {isExpanded && (
        <div id={bodyId} className="px-4 pb-4 pt-3 space-y-3 border-t border-border">
          {/* Why discovered — read-only */}
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Why discovered
            </p>
            <p className="text-sm text-muted-foreground leading-relaxed">
              {criterion.why_discovered}
            </p>
          </div>

          <Separator />

          <div>
            <Label htmlFor={`${uid}-name`} className="mb-1 block">Name</Label>
            <Input
              key={criterion.name}
              id={`${uid}-name`}
              defaultValue={criterion.name}
              onBlur={(e) => handleBlur("name", e.target.value)}
            />
          </div>

          {TEXT_FIELDS.map(({ key, label }) => (
            <div key={key}>
              <Label htmlFor={`${uid}-${key}`} className="mb-1 block">{label}</Label>
              <Textarea
                key={criterion[key]}
                id={`${uid}-${key}`}
                defaultValue={criterion[key]}
                onBlur={(e) => handleBlur(key, e.target.value)}
                className="min-h-[100px] resize-none"
              />
            </div>
          ))}

          <Separator />

          <TestsSection
            criterionSuite={criterionSuite}
            jobs={jobs}
            agentName={agentName}
            userRoleLabel={userRoleLabel}
            onReplay={onReplay}
            onBuildTests={onBuildTests}
          />
        </div>
      )}
    </div>
  );
}

const NO_JOBS = {};

export default function EvalCriteriaPanel({
  evalCriteria,
  patternState,
  discoverPending,
  onDismiss,
  onUpdate,
  onAdd,
  onExportAll,
  saveError,
  suite = null,
  jobs = NO_JOBS,
  agentName,
  userRoleLabel,
  onReplay,
  onBuildTests,
  onRunAll,
  onApprove,
  onReject,
  promptEditor,
  testsError = null,
  testsLoading = false,
  onRetryTests,
}) {
  const suiteByCriterion = new Map((suite?.criteria ?? []).map((c) => [c.criterion_id, c]));
  const hasTests = (suite?.criteria ?? []).some((c) => c.tests.length > 0);
  // A failed load must not make the tests vanish silently; a stale suite stays visible beside it.
  const showTestsError = Boolean(testsError) && !testsLoading;
  const candidate = suite?.candidate ?? null;
  const showStale = (suite?.stale_count ?? 0) > 0 && hasTests;
  const replayActive = Object.entries(jobs).some(
    ([key, job]) => key.startsWith("replay:") && (job.status === "queued" || job.status === "running")
  );

  // expandedIndex: only the last card is expanded by default
  const [expandedIndex, setExpandedIndex] = useState(evalCriteria.length - 1);

  // When the list grows (new criterion appended), expand the new last card
  useEffect(() => {
    setExpandedIndex(evalCriteria.length - 1);
  }, [evalCriteria.length]);

  function handleToggle(index) {
    setExpandedIndex((prev) => (prev === index ? null : index));
  }

  return (
    <div className="flex flex-col h-full bg-[#fafafa]">
      {/* Header */}
      <div className="h-11 flex items-center justify-between px-4 border-b border-border flex-shrink-0">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Eval criteria
        </h2>
        <Button
          variant="ghost"
          size="icon"
          onClick={onDismiss}
          className="h-8 w-8 text-muted-foreground"
          aria-label="Close eval criteria panel"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>

      {/* "New pattern detected" notification banner */}
      {patternState === "pattern_found" && evalCriteria.length > 0 && (
        <div className="px-4 py-2 bg-green-50 border-b border-green-200 text-xs text-green-800 flex-shrink-0">
          New pattern detected. Select Discover evals to add another criterion.
        </div>
      )}

      {saveError && (
        <div
          role="alert"
          className="px-4 py-2 bg-red-50 border-b border-red-200 text-xs text-red-700 flex-shrink-0"
        >
          {saveError}
        </div>
      )}

      {/* Scrollable criteria cards */}
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-3">
          {promptEditor && <PromptEditor {...promptEditor} />}

          {showTestsError && (
            <div
              role="alert"
              className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700"
            >
              <p>Couldn&apos;t load tests. {testsError}</p>
              <Button type="button" variant="outline" size="sm" onClick={onRetryTests}>
                Retry
              </Button>
            </div>
          )}

          {showStale && (
            <div
              role="status"
              className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900"
            >
              <p>{staleMessage(suite.stale_count)}</p>
              <Button type="button" variant="outline" size="sm" onClick={onRunAll} disabled={replayActive}>
                Run all tests
              </Button>
            </div>
          )}

          {candidate && (
            <ChangeReviewContainer
              key={`${candidate.version_id}:${candidate.status}`}
              candidate={candidate}
              jobs={jobs}
              onApprove={onApprove}
              onReject={onReject}
              agentName={agentName}
              userRoleLabel={userRoleLabel}
            />
          )}

          {promptEditor && <Separator />}

          {evalCriteria.map((criterion, index) => (
            <CriterionCard
              key={criterion.id}
              criterion={criterion}
              index={index}
              isExpanded={expandedIndex === index}
              onToggle={() => handleToggle(index)}
              onUpdate={onUpdate}
              criterionSuite={suiteByCriterion.get(criterion.id)}
              jobs={jobs}
              agentName={agentName}
              userRoleLabel={userRoleLabel}
              onReplay={onReplay}
              onBuildTests={onBuildTests}
            />
          ))}
        </div>
      </ScrollArea>

      {/* Fixed footer — Export is the filled action unless Discover evals is the next step */}
      <Separator />
      <div className="px-4 py-3 flex gap-2 flex-shrink-0">
        <Button
          variant={discoverPending ? "outline" : "accent"}
          className="flex-1"
          onClick={onExportAll}
        >
          Export all
        </Button>
        <Button variant="outline" className="flex-1" onClick={onAdd}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          Add criterion
        </Button>
      </div>
    </div>
  );
}
