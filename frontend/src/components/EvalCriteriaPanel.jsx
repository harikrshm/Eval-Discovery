import { useState, useEffect } from "react";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import { Label } from "./ui/label";
import { Separator } from "./ui/separator";
import { ScrollArea } from "./ui/scroll-area";
import { ChevronDown, ChevronUp } from "lucide-react";

function CriterionCard({ criterion, index, isExpanded, onToggle, onUpdate }) {
  function handleBlur(field, value) {
    onUpdate(criterion.id, { [field]: value });
  }

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-white">
      {/* Card header — criterion name Input inline + collapse toggle */}
      {/* Input click is stopped so it doesn't trigger the collapse toggle */}
      <div className="flex items-center gap-2 px-4 py-2.5">
        <span className="text-sm font-medium text-foreground flex-shrink-0">
          #{index + 1}
        </span>
        <Input
          defaultValue={criterion.name}
          onBlur={(e) => handleBlur("name", e.target.value)}
          onClick={(e) => e.stopPropagation()}
          className="flex-1 h-7 text-sm"
        />
        <button onClick={onToggle} className="flex-shrink-0 p-1 rounded hover:bg-muted-foreground/10">
          {isExpanded ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
        </button>
      </div>

      {isExpanded && (
        <div className="px-4 pb-4 pt-2 space-y-3 border-t border-border">
          {/* Why discovered — read-only */}
          <div>
            <Label className="mb-1 block">Why discovered</Label>
            <p className="text-xs text-muted-foreground leading-relaxed">
              {criterion.why_discovered}
            </p>
          </div>

          <Separator />

          {/* Definition, Good Example, Bad Example */}
          {[
            { key: "definition", label: "Definition" },
            { key: "good_example", label: "Good Example" },
            { key: "bad_example", label: "Bad Example" },
          ].map(({ key, label }) => (
            <div key={key}>
              <Label className="mb-1 block">{label}</Label>
              <Textarea
                defaultValue={criterion[key]}
                onBlur={(e) => handleBlur(key, e.target.value)}
                className="min-h-[100px] resize-none"
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function EvalCriteriaPanel({
  evalCriteria,
  patternState,
  onDismiss,
  onUpdate,
  onAdd,
  onExportAll,
}) {
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
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Eval Criteria
        </span>
        <Button
          variant="ghost"
          size="icon"
          onClick={onDismiss}
          className="h-7 w-7 text-muted-foreground"
          aria-label="Close panel"
        >
          ×
        </Button>
      </div>

      {/* "New pattern detected" notification banner */}
      {patternState === "pattern_found" && evalCriteria.length > 0 && (
        <div className="px-4 py-2 bg-green-50 border-b border-green-200 text-xs text-green-700 flex-shrink-0">
          ⚡ New pattern detected — click Discover Evals to add another criterion
        </div>
      )}

      {/* Scrollable criteria cards */}
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-3">
          {evalCriteria.map((criterion, index) => (
            <CriterionCard
              key={criterion.id}
              criterion={criterion}
              index={index}
              isExpanded={expandedIndex === index}
              onToggle={() => handleToggle(index)}
              onUpdate={onUpdate}
            />
          ))}
        </div>
      </ScrollArea>

      {/* Fixed footer */}
      <Separator />
      <div className="px-4 py-3 flex gap-2 flex-shrink-0">
        <Button className="flex-1" onClick={onExportAll}>
          Export All
        </Button>
        <Button className="flex-1" onClick={onAdd}>
          + Add Criterion
        </Button>
      </div>
    </div>
  );
}
