import { useState } from "react";
import { deleteWorkspace } from "../api";
import { clearAllDrafts } from "../lib/draft";
import { Button } from "./ui/button";

const CONFIRM_TEXT =
  "Delete all your agents, runs and criteria and start over? This cannot be undone.";

// Deletes the whole workspace (spec: users can remove their data), then lets the
// caller return to the first-run setup state.
export default function DeleteDataControl({ onDeleted, variant = "outline", className }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleClick() {
    if (busy) return;
    if (!window.confirm(CONFIRM_TEXT)) return;
    setBusy(true);
    setError(null);
    try {
      await deleteWorkspace();
      clearAllDrafts();
      onDeleted();
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : "Couldn't delete your data. Try again.");
      setBusy(false);
    }
  }

  return (
    <div className={className}>
      <Button type="button" variant={variant} size="sm" onClick={handleClick} disabled={busy}>
        {busy ? "Deleting…" : "Delete my data / start over"}
      </Button>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
