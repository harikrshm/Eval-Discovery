import { useState, useEffect } from "react";

function CriterionCard({ criterion, index, isExpanded, onToggle, onUpdate }) {
  function handleBlur(field, value) {
    onUpdate(criterion.id, { [field]: value });
  }

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 text-left hover:bg-gray-100"
        onClick={onToggle}
      >
        <span className="text-sm font-medium text-gray-800">
          #{index + 1} {criterion.name}
        </span>
        <span className="text-gray-400 text-xs ml-2 flex-shrink-0">
          {isExpanded ? "▲" : "▼"}
        </span>
      </button>

      {isExpanded && (
        <div className="p-4 space-y-3 border-t border-gray-200">
          {/* Why discovered — read-only */}
          <div>
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">
              Why discovered
            </p>
            <p className="text-xs text-gray-600 leading-relaxed">
              {criterion.why_discovered}
            </p>
          </div>

          {/* Name (editable) */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
              Name
            </label>
            <input
              type="text"
              defaultValue={criterion.name}
              onBlur={(e) => handleBlur("name", e.target.value)}
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white"
            />
          </div>

          {/* Definition, Good example, Bad example (editable textareas) */}
          {[
            { key: "definition", label: "Definition" },
            { key: "good_example", label: "Good Example" },
            { key: "bad_example", label: "Bad Example" },
          ].map(({ key, label }) => (
            <div key={key}>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                {label}
              </label>
              <textarea
                defaultValue={criterion[key]}
                onBlur={(e) => handleBlur(key, e.target.value)}
                rows={3}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white resize-none"
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
  // Track which card is expanded: last card expanded by default
  const [expandedIndex, setExpandedIndex] = useState(evalCriteria.length - 1);

  // When list grows (new criterion added), expand the new last card
  useEffect(() => {
    setExpandedIndex(evalCriteria.length - 1);
  }, [evalCriteria.length]);

  function handleToggle(index) {
    setExpandedIndex((prev) => (prev === index ? null : index));
  }

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between flex-shrink-0">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Eval Criteria
        </span>
        <button
          onClick={onDismiss}
          className="text-gray-400 hover:text-gray-600 text-lg leading-none"
          aria-label="Close panel"
        >
          ×
        </button>
      </div>

      {/* New pattern notification banner */}
      {patternState === "pattern_found" && evalCriteria.length > 0 && (
        <div className="px-4 py-2 bg-green-50 border-b border-green-200 text-xs text-green-700 flex-shrink-0">
          ⚡ New pattern detected — click Discover Evals to add another criterion
        </div>
      )}

      {/* Scrollable criteria cards */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
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

      {/* Fixed footer */}
      <div className="px-4 py-3 border-t border-gray-200 flex gap-2 flex-shrink-0">
        <button
          onClick={onExportAll}
          className="flex-1 bg-gray-800 text-white py-2 rounded-lg text-sm font-medium hover:bg-gray-700"
        >
          Export All
        </button>
        <button
          onClick={onAdd}
          className="px-4 py-2 border border-gray-300 rounded-lg text-sm text-gray-600 hover:bg-gray-100"
        >
          + Add Criteria
        </button>
      </div>
    </div>
  );
}
