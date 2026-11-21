import { useState, useEffect, useId, useRef } from "react";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { Label } from "./ui/label";

const PROMPT_MAX = 4000;
const STALE_PROMPT_ERROR = "Couldn't load the latest prompt. Try again.";
const VERIFYING_TEXT = "A fix is being verified. Wait for it to finish.";

// Edits the agent's system prompt. Saving creates a new prompt version; the parent's
// onSave(text, baseVersionId) is async and throws an Error whose message is shown here.
// `versionId` is the live version; the first keystroke records it as the version the edit is based
// on. If the live version moves on while that draft exists (a fix was promoted, a version
// restored), the draft is flagged and cannot be saved until it is discarded, because saving it
// would silently replace the newer prompt. The server refuses such a save too (409).
// `verifyingNumber` is the number of a suggested fix that is being verified (null otherwise):
// the server refuses edits meanwhile, so the editor does too. `onBeforeEdit` reloads the live
// prompt and resolves true when it is current; the editor never opens on a prompt it could not
// confirm, because saving from an old text would overwrite a newer version.
export default function PromptEditor({
  promptText,
  versionNumber,
  versionId,
  verifyingNumber = null,
  onSave,
  onBeforeEdit,
}) {
  const [editing, setEditing] = useState(false);
  // null until the user types, so the text always starts from (and follows) the live prompt.
  const [draft, setDraft] = useState(null);
  const [baseId, setBaseId] = useState(null); // the live version when the draft was started
  const [saving, setSaving] = useState(false);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState(null);
  const [error, setError] = useState(null);
  const [justSaved, setJustSaved] = useState(false);

  const text = draft ?? promptText;
  const verifying = verifyingNumber != null;
  const stale = editing && draft !== null && baseId !== versionId;
  // An untouched editor shows the live prompt, so it is always based on the live version.
  const base = draft === null ? versionId : baseId;
  const verifyingRef = useRef(verifying);
  const openingRef = useRef(false);
  const refocusAfterOpen = useRef(false);
  const refocusText = useRef(false);
  const textRef = useRef(null);
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

  // Discard removes the notice (and its focused button); focus moves to the textarea, which now
  // shows the live prompt.
  useEffect(() => {
    if (!stale && refocusText.current) {
      refocusText.current = false;
      textRef.current?.focus();
    }
  }, [stale]);

  useEffect(() => {
    verifyingRef.current = verifying;
  }, [verifying]);

  // The button is disabled while the latest prompt loads, which drops its focus; give it back.
  useEffect(() => {
    if (!opening && refocusAfterOpen.current) {
      refocusAfterOpen.current = false;
      editRef.current?.focus();
    }
  }, [opening]);

  const trimmed = text.trim();
  const canSave = !saving && !verifying && !stale && trimmed.length > 0 && trimmed !== promptText.trim() && text.length <= PROMPT_MAX;

  async function open() {
    if (editing) {
      cancel();
      return;
    }
    if (openingRef.current || verifying) return;
    setOpenError(null);
    if (onBeforeEdit) {
      openingRef.current = true;
      refocusAfterOpen.current = document.activeElement === editRef.current;
      setOpening(true);
      let current = false;
      try {
        current = (await onBeforeEdit()) === true;
      } catch {
        current = false;
      }
      openingRef.current = false;
      if (!mounted.current) return;
      setOpening(false);
      if (!current) {
        setOpenError(STALE_PROMPT_ERROR);
        return;
      }
      if (verifyingRef.current) return; // a fix started verifying during the reload
    }
    setDraft(null);
    setError(null);
    setJustSaved(false);
    setEditing(true);
  }

  function handleChange(value) {
    if (draft === null) setBaseId(versionId);
    setDraft(value);
  }

  // Drops the draft and shows the live prompt again.
  function discard() {
    refocusText.current = true;
    setDraft(null);
    setError(null);
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
      await onSave(trimmed, base);
      if (!mounted.current) return;
      refocus.current = true;
      setEditing(false);
      setJustSaved(true);
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
        <div>
          <p className="text-sm text-foreground">
            Chat uses v{versionNumber}
            {verifying && ` · v${verifyingNumber} verifying`}
          </p>
          {/* Always rendered so the announcement of a save is picked up when its text appears. */}
          <p role="status" className="sr-only">
            {justSaved ? `Saved as v${versionNumber}.` : ""}
          </p>
        </div>
        <Button
          ref={editRef}
          type="button"
          variant="outline"
          size="sm"
          onClick={open}
          disabled={verifying || opening}
          aria-busy={opening}
          aria-expanded={editing}
          aria-controls={formId}
        >
          Edit prompt
        </Button>
      </div>

      {verifying && <p className="text-xs text-muted-foreground">{VERIFYING_TEXT}</p>}

      {openError && (
        <div role="alert" className="text-sm text-red-700">
          {openError}
        </div>
      )}

      {editing && (
        <form id={formId} onSubmit={handleSave} className="space-y-2">
          <div>
            <Label htmlFor={textId} className="mb-1 block">System prompt</Label>
            <Textarea
              id={textId}
              ref={textRef}
              value={text}
              onChange={(e) => handleChange(e.target.value)}
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

          {stale && (
            <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-red-700">
              <p>
                The prompt changed to v{versionNumber} while you were editing. Your edit is based on an older version.
              </p>
              <Button type="button" variant="outline" size="sm" onClick={discard}>
                Discard edit
              </Button>
            </div>
          )}

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
