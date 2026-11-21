import { Fragment, useState, useId, useMemo } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { diffLines } from "../lib/diff";
import { verdictLabel } from "../lib/testStatus";
import { cn } from "../lib/utils";

// Pieces of the change review. Everything the model wrote (the why summary, row rationales) is
// untrusted and only ever rendered as plain React text children, never as markup.

const ROLE_LABELS = { fix: "Fix test", heldout: "Held-out test", regression: "Previously passing" };

const WHY_FIELDS = [
  { key: "criterion_name", label: "Criterion" },
  { key: "change_summary", label: "The change" },
  { key: "touched_section", label: "Part of the prompt it touches" },
  { key: "expected_effect", label: "Expected effect" },
  { key: "risk", label: "Risk" },
];

const DIFF_PREFIX = { added: "+ ", removed: "- ", same: "  " };
const DIFF_LABEL = { added: "Added line: ", removed: "Removed line: " };
const DIFF_STYLE = { added: "bg-green-50", removed: "bg-red-50", same: "" };

export function Diff({ parentText, text }) {
  const lines = useMemo(() => diffLines(parentText, text), [parentText, text]);
  return (
    <ul
      role="list"
      aria-label="Prompt changes"
      className="mt-1 max-h-72 overflow-auto rounded-md border border-border bg-white py-1 font-mono text-xs"
    >
      {lines.map((line, i) => (
        <li key={i} className={cn("whitespace-pre-wrap break-words px-2", DIFF_STYLE[line.type])}>
          {DIFF_LABEL[line.type] && <span className="sr-only">{DIFF_LABEL[line.type]}</span>}
          <span aria-hidden="true">{DIFF_PREFIX[line.type]}</span>
          {line.text}
        </li>
      ))}
    </ul>
  );
}

export function Why({ why }) {
  return (
    <div className="mt-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Why this change</h4>
      {why.failing_quote && (
        <div className="mt-1">
          <p className="text-xs font-semibold text-muted-foreground">Where it went wrong</p>
          <blockquote className="mt-0.5 border-l-2 border-border pl-2 text-sm text-foreground break-words">
            {why.failing_quote}
          </blockquote>
        </div>
      )}
      <dl className="mt-1 space-y-1">
        {WHY_FIELDS.filter(({ key }) => why[key]).map(({ key, label }) => (
          <div key={key}>
            <dt className="text-xs font-semibold text-muted-foreground">{label}</dt>
            <dd className="text-sm text-foreground break-words">{why[key]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function OutcomeRow({ row, agentName, userRoleLabel }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const label = ROLE_LABELS[row.role] ?? "Test";
  const name = row.criterion_name ? `${label}: ${row.criterion_name}` : label;
  const transcript = Array.isArray(row.transcript) ? row.transcript : [];

  return (
    <Fragment>
      <tr className="border-t border-border align-top">
        <td className="py-1.5 pr-2">
          <p className="font-medium text-foreground">{label}</p>
          {row.criterion_name && <p className="text-muted-foreground break-words">{row.criterion_name}</p>}
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls={detailsId}
            className="mt-0.5 inline-flex items-center gap-1 rounded text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
          >
            Details
            <span className="sr-only"> for {name}</span>
            {open ? (
              <ChevronUp className="h-3 w-3" aria-hidden="true" />
            ) : (
              <ChevronDown className="h-3 w-3" aria-hidden="true" />
            )}
          </button>
        </td>
        <td className="py-1.5 pr-2">{verdictLabel(row.before)}</td>
        <td className="py-1.5 font-medium">{verdictLabel(row.after)}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={3} className="pb-2">
            <div id={detailsId} className="space-y-2 rounded-md bg-muted p-2 text-foreground">
              {row.rationale && (
                <div>
                  <p className="font-semibold text-muted-foreground">Judge&apos;s reasoning</p>
                  <p className="mt-0.5 leading-relaxed break-words">{row.rationale}</p>
                </div>
              )}
              {transcript.length > 0 && (
                <div>
                  <p className="font-semibold text-muted-foreground">Transcript</p>
                  <div className="mt-0.5 space-y-1">
                    {transcript.map((message, i) => (
                      <p key={i} className="leading-relaxed break-words">
                        <span className="font-medium">
                          {message.role === "user" ? userRoleLabel : agentName}:
                        </span>{" "}
                        {message.content}
                      </p>
                    ))}
                  </div>
                </div>
              )}
              {!row.rationale && transcript.length === 0 && <p className="text-muted-foreground">No details stored.</p>}
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export function OutcomeTable({ table, agentName, userRoleLabel }) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Test results before and after the change</caption>
        <thead>
          <tr className="text-muted-foreground">
            <th scope="col" className="pb-1 pr-2 font-semibold">Test</th>
            <th scope="col" className="pb-1 pr-2 font-semibold">Before</th>
            <th scope="col" className="pb-1 font-semibold">After</th>
          </tr>
        </thead>
        <tbody>
          {table.map((row, i) => (
            <OutcomeRow key={row.test_id ?? i} row={row} agentName={agentName} userRoleLabel={userRoleLabel} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
