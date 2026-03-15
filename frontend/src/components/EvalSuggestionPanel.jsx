import { useState, useEffect } from "react";

export default function EvalSuggestionPanel({ rubric, intent, onDismiss, onSave }) {
  const [edited, setEdited] = useState(rubric);

  useEffect(() => {
    setEdited(rubric);
  }, [rubric]);

  function handleChange(field, value) {
    setEdited((prev) => ({ ...prev, [field]: value }));
  }

  const intentLabel = intent?.replace(/_/g, " ") ?? "";

  return (
    <div className="border-l bg-amber-50 flex flex-col h-full">
      <div className="px-4 py-3 bg-amber-100 border-b border-amber-200 flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide">
            Eval Suggestion
          </p>
          <p className="text-sm text-amber-900 mt-0.5">
            We noticed you're testing <strong>{intentLabel}</strong>. Here's a rubric to evaluate it.
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
