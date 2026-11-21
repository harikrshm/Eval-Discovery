import { useState, useEffect, useId, useRef } from "react";
import { listPresets, createAgent } from "../api";
import { getToken, setAgentId } from "../lib/workspace";
import { isAgentLimitError } from "../lib/chatLimits";
import { buildAgentPayload } from "../lib/agentPayload";
import DeleteDataControl from "./DeleteDataControl";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";

const NAME_MAX = 60;
const PROMPT_MAX = 4000;
const ROLE_MAX = 40;

// Custom agents are analysed by an LLM, which can be slow on a cold server.
const SLOW_CREATE_MS = 4000;

function messageOf(err, fallback) {
  return err instanceof Error && err.message ? err.message : fallback;
}

export default function AgentSetup({ onCreated, onCancel, onDeleted, notice }) {
  const [presets, setPresets] = useState([]);
  const [presetsState, setPresetsState] = useState("loading"); // loading | ready | error
  const [preset, setPreset] = useState(null);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [roleLabel, setRoleLabel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [slow, setSlow] = useState(false);
  const [error, setError] = useState(null);
  const [limitHit, setLimitHit] = useState(false);
  const [presetsAttempt, setPresetsAttempt] = useState(0);

  const nameId = useId();
  const promptId = useId();
  const counterId = useId();
  const roleId = useId();
  const roleHintId = useId();
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    listPresets()
      .then((list) => {
        if (cancelled) return;
        setPresets(Array.isArray(list) ? list : []);
        setPresetsState("ready");
      })
      .catch(() => {
        if (!cancelled) setPresetsState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [presetsAttempt]);

  useEffect(() => {
    if (!submitting) return undefined;
    const timer = setTimeout(() => setSlow(true), SLOW_CREATE_MS);
    return () => clearTimeout(timer);
  }, [submitting]);

  function retryPresets() {
    setPresetsState("loading");
    setPresetsAttempt((n) => n + 1);
  }

  function pickPreset(p) {
    setPreset(p);
    setName(p.name);
    setPrompt(p.system_prompt);
    setRoleLabel(p.user_role_label ?? "");
    setError(null);
  }

  function pickBlank() {
    setPreset(null);
    setName("");
    setPrompt("");
    setRoleLabel("");
    setError(null);
  }

  // Only offer deletion when this browser has a workspace to delete.
  const hasStoredData = Boolean(getToken());

  const canSubmit = name.trim().length > 0 && prompt.trim().length > 0;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!canSubmit || submitting) return;

    setSubmitting(true);
    setSlow(false);
    setError(null);
    setLimitHit(false);
    try {
      const agent = await createAgent(buildAgentPayload(preset, { name, prompt, roleLabel }));
      // Cancelled while the request was in flight: do not switch agents behind the user's back.
      if (!mounted.current) return;
      setAgentId(agent.id);
      onCreated(agent);
    } catch (err) {
      if (mounted.current) {
        setError(messageOf(err, "Couldn't create the agent. Try again."));
        setLimitHit(isAgentLimitError(err));
      }
    } finally {
      if (mounted.current) {
        setSubmitting(false);
        setSlow(false);
      }
    }
  }

  return (
    <div className="h-dvh overflow-y-auto bg-background">
      <main className="mx-auto w-full max-w-xl px-4 py-8 sm:px-6 sm:py-12">
        <h1 className="text-base font-semibold tracking-wide text-foreground">Eval Discovery</h1>
        <h2 className="mt-6 text-lg font-semibold text-foreground">Set up the agent you want to test</h2>
        <p className="mt-2 text-muted-foreground">
          Start from an example or write your own system prompt. You chat with the agent, and
          Eval Discovery turns repeated test conversations into evals.
        </p>

        {notice && (
          <div role="alert" className="mt-4 text-sm text-red-700">
            {notice}
          </div>
        )}

        <section aria-labelledby="presets-heading" className="mt-6">
          <h3 id="presets-heading" className="text-sm font-medium text-foreground">
            Start from
          </h3>
          <div className="mt-2 flex flex-wrap gap-2">
            {presets.map((p) => (
              <Button
                key={p.id}
                type="button"
                variant="outline"
                size="sm"
                aria-pressed={preset?.id === p.id}
                className={preset?.id === p.id ? "border-[var(--ring)] ring-2 ring-[var(--ring)]" : undefined}
                disabled={submitting}
                onClick={() => pickPreset(p)}
              >
                {p.name}
              </Button>
            ))}
            <Button type="button" variant="outline" size="sm" disabled={submitting} onClick={pickBlank}>
              Blank
            </Button>
          </div>
          {presetsState === "loading" && (
            <p role="status" className="mt-2 text-sm text-muted-foreground">
              Loading examples…
            </p>
          )}
          {presetsState === "error" && (
            <div role="alert" className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-red-700">
              <span>Couldn&apos;t load the examples. You can still write your own.</span>
              <Button type="button" variant="outline" size="sm" onClick={retryPresets}>
                Try again
              </Button>
            </div>
          )}
        </section>

        <form onSubmit={handleSubmit} className="mt-6 space-y-5">
          <div>
            <label htmlFor={nameId} className="text-sm font-medium text-foreground">
              Name
            </label>
            <Input
              id={nameId}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={NAME_MAX}
              autoComplete="off"
              className="mt-1.5"
            />
          </div>

          <div>
            <label htmlFor={promptId} className="text-sm font-medium text-foreground">
              System prompt
            </label>
            <Textarea
              id={promptId}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              maxLength={PROMPT_MAX}
              aria-describedby={counterId}
              rows={9}
              className="mt-1.5 resize-y"
            />
            <p id={counterId} className="mt-1 text-xs text-muted-foreground">
              {prompt.length} / {PROMPT_MAX} characters
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Your prompts and chats are stored for 30 days, tied to this browser.
            </p>
          </div>

          <div>
            <label htmlFor={roleId} className="text-sm font-medium text-foreground">
              You play <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <Input
              id={roleId}
              value={roleLabel}
              onChange={(e) => setRoleLabel(e.target.value)}
              maxLength={ROLE_MAX}
              aria-describedby={roleHintId}
              autoComplete="off"
              className="mt-1.5"
            />
            <p id={roleHintId} className="mt-1 text-xs text-muted-foreground">
              Who you are in the conversation, such as Customer. Leave it empty to let us infer it.
            </p>
          </div>

          {error && (
            <div role="alert" className="text-sm text-red-700">
              <p>{error}</p>
              {limitHit && onDeleted && (
                <div className="mt-2">
                  <p>Delete your data to clear your agents and start over.</p>
                  <DeleteDataControl onDeleted={onDeleted} className="mt-2" />
                </div>
              )}
            </div>
          )}

          {submitting && slow && (
            <p role="status" className="text-sm text-muted-foreground">
              Analyzing your agent. The first request can take up to a minute.
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="submit"
              variant="accent"
              disabled={!canSubmit}
              aria-busy={submitting}
            >
              {submitting ? "Creating…" : error ? "Try again" : "Create agent"}
            </Button>
            {onCancel && (
              <Button type="button" variant="outline" onClick={onCancel}>
                Cancel
              </Button>
            )}
          </div>
        </form>

        {onDeleted && hasStoredData && !limitHit && (
          <section aria-labelledby="delete-heading" className="mt-10 border-t border-border pt-6">
            <h3 id="delete-heading" className="text-sm font-medium text-foreground">
              Your data
            </h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Remove all your agents, runs and criteria from this browser and our server.
            </p>
            <DeleteDataControl onDeleted={onDeleted} className="mt-3" />
          </section>
        )}
      </main>
    </div>
  );
}
