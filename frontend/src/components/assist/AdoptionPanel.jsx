import { useEffect, useRef, useState } from "react";
import { assistFlagged } from "../../api";
import { Button } from "../ui/button";
import { BarList } from "./Bars";
import FlaggedList from "./FlaggedList";
import InsightsBrief from "./InsightsBrief";
import VersionComparison from "./VersionComparison";
import { NA, humanize, isNumber, pct, per100 } from "./format";

const FLAGGED_LIMIT = 50;
const NO_FILTER = { intent: null, section: null };

const DEFINITIONS = [
  "Coverage = share of non-bounce page views where the section was visible for at least 3 seconds.",
  "Help demand = questions about a section per 100 views of it. It can exceed 100.",
  'Thumbs-down = "down" ratings only.',
  "Refusals = legal-opinion questions.",
  "Widget usage is NOT measured.",
];

function Section({ id, title, children }) {
  return (
    <section aria-labelledby={id} className="space-y-3">
      <h3 id={id} className="text-sm font-semibold text-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}

// A table cell that is a button when the row is a hotspot: it sets the flagged-list filter.
function HotspotName({ name, kind, hotspot, onPick }) {
  if (!hotspot) return <span>{humanize(name)}</span>;
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={() => onPick(kind, name)}
      aria-label={`Show flagged conversations for ${kind} ${humanize(name)}`}
    >
      {humanize(name)}
    </Button>
  );
}

function positive(value) {
  return isNumber(value) && value > 0;
}

function useFlagged(filter, reloadKey) {
  const key = `${filter.intent ?? ""}|${filter.section ?? ""}|${reloadKey}`;
  const [result, setResult] = useState({ key: null, items: null, error: null });

  useEffect(() => {
    let cancelled = false;
    assistFlagged({
      intent: filter.intent ?? undefined,
      section: filter.section ?? undefined,
      limit: FLAGGED_LIMIT,
    })
      .then((items) => {
        if (!cancelled) setResult({ key, items, error: null });
      })
      .catch((err) => {
        if (cancelled) return;
        const error = err instanceof Error && err.message ? err.message : "Something went wrong.";
        setResult({ key, items: null, error });
      });
    return () => {
      cancelled = true;
    };
  }, [key, filter.intent, filter.section]);

  const current = result.key === key;
  return { items: current ? result.items : null, error: current ? result.error : null, loading: !current };
}

// The Adoption tab: how Google Patents is used and where people struggle, all from the metrics
// response. Rows that show a problem are buttons that filter the flagged list below.
export default function AdoptionPanel({ metrics, reloadKey, onReview }) {
  const [filter, setFilter] = useState(NO_FILTER);
  const [retry, setRetry] = useState(0);
  const flaggedRef = useRef(null);
  const flagged = useFlagged(filter, `${reloadKey}:${retry}`);

  const adoption = metrics.adoption ?? {};
  const coverage = adoption.feature_coverage ?? {};
  const sections = Object.keys(coverage);
  const views = adoption.views_by_section ?? {};
  const helpDemand = adoption.help_demand ?? {};
  const withoutViews = Object.entries(adoption.questions_without_views ?? {});
  const taskMix = Object.entries(adoption.task_mix ?? {});
  const struggle = adoption.struggle ?? {};
  const reask = struggle.reask_rate_by_intent ?? {};
  const thumbsDown = struggle.thumbs_down_rate_by_intent ?? {};

  function pick(kind, name) {
    setFilter(kind === "section" ? { intent: null, section: name } : { intent: name, section: null });
    flaggedRef.current?.focus();
    flaggedRef.current?.scrollIntoView?.({ block: "start" });
  }

  return (
    <div className="space-y-8">
      <Section id="coverage-heading" title="Feature coverage">
        <BarList
          label="Feature coverage by section"
          items={sections.map((name) => ({ key: name, label: humanize(name), value: coverage[name] }))}
        />
      </Section>

      <Section id="help-heading" title="Help demand">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Help demand by section</caption>
            <thead>
              <tr className="text-xs uppercase tracking-wide text-muted-foreground">
                <th scope="col" className="py-1 pr-4 font-medium">Section</th>
                <th scope="col" className="py-1 pr-4 font-medium">Views</th>
                <th scope="col" className="py-1 pr-4 font-medium">Questions per 100 views</th>
              </tr>
            </thead>
            <tbody>
              {sections.map((name) => (
                <tr key={name}>
                  <th scope="row" className="py-1 pr-4 font-normal">
                    <HotspotName name={name} kind="section" hotspot={positive(helpDemand[name])} onPick={pick} />
                  </th>
                  <td className="py-1 pr-4 tabular-nums">{views[name] ?? 0}</td>
                  <td className="py-1 pr-4 tabular-nums">{per100(helpDemand[name])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {withoutViews.length > 0 && (
          <p className="text-sm text-muted-foreground">
            Questions about sections with no recorded views:{" "}
            {withoutViews.map(([name, count]) => `${humanize(name)} (${count})`).join(", ")}
          </p>
        )}
      </Section>

      <Section id="mix-heading" title="Task mix">
        <BarList
          label="Task mix by intent"
          items={taskMix.map(([intent, row]) => ({
            key: intent,
            label: humanize(intent),
            value: row?.share ?? null,
            detail: `${row?.count ?? 0}`,
          }))}
        />
      </Section>

      <Section id="struggle-heading" title="Where people struggle">
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Navigation help questions</dt>
          <dd className="tabular-nums">{struggle.navigation_help_count ?? NA}</dd>
          <dt className="text-muted-foreground">Refusals</dt>
          <dd className="tabular-nums">{struggle.refusal_count ?? NA}</dd>
          <dt className="text-muted-foreground">Unverified citation rate</dt>
          <dd className="tabular-nums">{pct(struggle.unverified_citation_rate)}</dd>
          <dt className="text-muted-foreground">Bounce rate</dt>
          <dd className="tabular-nums">{pct(adoption.bounce_rate)}</dd>
        </dl>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Re-ask and thumbs-down rate by task</caption>
            <thead>
              <tr className="text-xs uppercase tracking-wide text-muted-foreground">
                <th scope="col" className="py-1 pr-4 font-medium">Task</th>
                <th scope="col" className="py-1 pr-4 font-medium">Re-ask rate</th>
                <th scope="col" className="py-1 pr-4 font-medium">Thumbs-down rate</th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(reask).map((intent) => (
                <tr key={intent}>
                  <th scope="row" className="py-1 pr-4 font-normal">
                    <HotspotName
                      name={intent}
                      kind="task"
                      hotspot={positive(reask[intent])}
                      onPick={(_, name) => pick("intent", name)}
                    />
                  </th>
                  <td className="py-1 pr-4 tabular-nums">{pct(reask[intent])}</td>
                  <td className="py-1 pr-4 tabular-nums">{pct(thumbsDown[intent])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <section aria-labelledby="definitions-heading" className="rounded-md border border-border bg-muted/40 p-4">
        <h3 id="definitions-heading" className="text-sm font-semibold text-foreground">
          Definitions
        </h3>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          {DEFINITIONS.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>

      <InsightsBrief />

      <VersionComparison comparison={metrics.version_comparison} />

      <FlaggedList
        ref={flaggedRef}
        items={flagged.items}
        loading={flagged.loading}
        error={flagged.error}
        filter={filter}
        onClearFilter={() => setFilter(NO_FILTER)}
        onRetry={() => setRetry((n) => n + 1)}
        onReview={onReview}
      />
    </div>
  );
}
