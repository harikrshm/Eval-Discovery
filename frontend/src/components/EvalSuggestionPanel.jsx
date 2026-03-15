import { useState, useEffect } from "react";

export default function EvalSuggestionPanel({ rubric, onDismiss, onSave }) {
  const [edited, setEdited] = useState(rubric);

  useEffect(() => {
    setEdited(rubric);
  }, [rubric]);

  function handleChange(field, value) {
    setEdited((prev) => ({ ...prev, [field]: value }));
  }

  return (
    <div className="border-l bg-amber-50 flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 bg-amber-100 border-b border-amber-200 flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide">
            Eval Suggestion
          </p>
          <p className="text-sm font-medium text-amber-900 mt-0.5">
            {edited.name}
          </p>
        </div>
        <button
          onClick={onDismiss}
          className="text-amber-500 hover:text-amber-700 text-lg leading-none flex-shrink-0"
          aria-label="Dismiss"
        >
          ×
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Why discovered — read-only */}
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-3 py-2">
          <p className="text-xs font-semibold text-blue-700 mb-1">Why this was discovered</p>
          <p className="text-xs text-blue-800">{edited.why_discovered}</p>
        </div>

        {/* Editable rubric fields */}
        {[
          { key: "name", label: "Rubric Name", multiline: false },
          { key: "definition", label: "Definition", multiline: true },
          { key: "good_example", label: "Good Example", multiline: true },
          { key: "bad_example", label: "Bad Example", multiline: true },
        ].map(({ key, label, multiline }) => (
          <div key={key}>
            <label className="block text-xs font-semibold text-gray-600 mb-1">
              {label}
            </label>
            {multiline ? (
              <textarea
                value={edited[key]}
                onChange={(e) => handleChange(key, e.target.value)}
                rows={4}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400 bg-white resize-none"
              />
            ) : (
              <input
                type="text"
                value={edited[key]}
                onChange={(e) => handleChange(key, e.target.value)}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400 bg-white"
              />
            )}
          </div>
        ))}
      </div>

      <div className="px-4 py-3 border-t border-amber-200 flex gap-2">
        <button
          onClick={() => onSave(edited)}
          className="flex-1 bg-amber-500 text-white py-2 rounded-lg text-sm font-medium hover:bg-amber-600"
        >
          Save Rubric
        </button>
        <button
          onClick={onDismiss}
          className="px-4 py-2 border border-gray-300 rounded-lg text-sm text-gray-600 hover:bg-gray-100"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
