import { useEffect, useId, useRef, useState } from "react";
import { ApiError, assistStatus } from "../../api";
import { beginAssistSwap, completeAssistSwap, restoreAssistBackup } from "../../lib/workspace";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";

export const NOT_ASSIST_WORKSPACE = "That token does not belong to a Patent Assist workspace.";
const UNREACHABLE = "Couldn't reach the server. Try again.";

// Asks for the demo workspace token and swaps the app over to it. The token is only ever held in
// this input (cleared as soon as it is used), in storage under the app's token key, and in the
// request header: it is never rendered, logged or put in a URL. A refused token puts the previous
// workspace and agent back untouched.
export default function ConnectBox({ onConnected, onClose, returnFocusRef }) {
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);
  const inputId = useId();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key !== "Escape") return;
      onClose();
      returnFocusRef?.current?.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, returnFocusRef]);

  async function submit(e) {
    e.preventDefault();
    const value = token.trim();
    if (!value || pending) return;
    setPending(true);
    setError(null);
    setToken("");
    beginAssistSwap(value);
    let status;
    try {
      status = await assistStatus();
      if (!status || typeof status.agent_id !== "string" || !status.agent_id) {
        throw new ApiError("Unexpected response", 404);
      }
    } catch (err) {
      restoreAssistBackup();
      const refused = err instanceof ApiError && (err.status === 401 || err.status === 404);
      setError(refused ? NOT_ASSIST_WORKSPACE : UNREACHABLE);
      setPending(false);
      return;
    }
    completeAssistSwap(status.agent_id);
    onConnected(status);
  }

  return (
    <div
      role="dialog"
      aria-label="Connect to the Assist console"
      className="fixed right-4 top-16 z-50 w-[min(22rem,calc(100vw-2rem))] rounded-lg border border-border bg-white p-4 shadow-xl"
    >
      <form onSubmit={submit} className="space-y-3">
        <div>
          <Label htmlFor={inputId} className="mb-1 block">
            Demo workspace token
          </Label>
          <Input
            id={inputId}
            ref={inputRef}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            disabled={pending}
          />
        </div>
        <div role="status" aria-live="polite" className="text-xs text-muted-foreground">
          {pending ? "Connecting…" : ""}
        </div>
        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              onClose();
              returnFocusRef?.current?.focus();
            }}
          >
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={pending || token.trim() === ""}>
            Connect
          </Button>
        </div>
      </form>
    </div>
  );
}
