import { useState, useEffect, useId, useRef } from "react";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { Label } from "./ui/label";

const PROMPT_MAX = 4000;

// Edits the agent's system prompt. Saving creates a new prompt version; the parent's
// onSave(text) is async and throws an Error whose message is shown here.
export default function PromptEditor({ promptText, versionNumber, onSave }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(promptText);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const editRef = useRef(null);
  const mounted = useRef(true);
  const refocus = useRef(false);
  const textId = useId();
  const counterId = useId();
  const formId = useId();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Leaving the form (save or cancel) unmounts the focused control, so focus goes back
  // to the button that opened it.
  useEffect(() => {
    if (!editing && refocus.current) {
      refocus.current = false;
      editRef.current?.focus();
    }
  }, [editing]);

  const trimmed = text.trim();
  const canSave = !saving && trimmed.length > 0 && trimmed !== promptText.trim() && text.length <= PROMPT_MAX;

  function open() {
    if (editing) {
      cancel();
      return;
    }
    setText(promptText);
    setError(null);
    setEditing(true);
  }

  function cancel() {
    if (saving) return;
    refocus.current = true;
    setEditing(false);
    setError(null);
  }

  async function handleSave(e) {
    e.preventDefault();
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      await onSave(trimmed);
      if (!mounted.current) return;
      refocus.current = true;
      setEditing(false);
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error && err.message ? err.message : "Couldn't save the prompt. Try again.");
      }
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  return (
    <section aria-label="Agent prompt" className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-foreground">Chat uses prompt v{versionNumber}</p>
        <Button
          ref={editRef}
          type="button"
          variant="outline"
          size="sm"
          onClick={open}
          aria-expanded={editing}
          aria-controls={formId}
        >
          Edit prompt
        </Button>
      </div>

      {editing && (
        <form id={formId} onSubmit={handleSave} className="space-y-2">
          <div>
            <Label htmlFor={textId} className="mb-1 block">System prompt</Label>
            <Textarea
              id={textId}
              value={text}
              onChange={(e) => setText(e.target.value)}
              readOnly={saving}
              aria-describedby={counterId}
              rows={9}
              className="resize-y"
            />
            <p id={counterId} className="mt-1 text-xs text-muted-foreground">
              {text.length} / {PROMPT_MAX}
              {text.length > PROMPT_MAX && " (too long)"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Saving creates a new prompt version. Your tests then need to be run again on it.
            </p>
          </div>

          {error && (
            <div role="alert" className="text-sm text-red-700">
              {error}
            </div>
          )}

          <div className="flex items-center gap-2">
            <Button type="submit" variant="accent" size="sm" disabled={!canSave} aria-busy={saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={cancel} disabled={saving}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
