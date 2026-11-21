import { useState, useEffect, useCallback, useRef } from "react";
import {
  ApiError,
  getAgentState,
  saveRun as apiSaveRun,
  checkPattern as apiCheckPattern,
  discoverCriterion as apiDiscoverCriterion,
  addCriterion as apiAddCriterion,
  updateCriterion as apiUpdateCriterion,
  updatePrompt as apiUpdatePrompt,
} from "../api";
import { clearAgentId, clearWorkspace } from "../lib/workspace";
import { derivePatternState } from "../lib/patternState";
import { draftKey, remainingDraft } from "../lib/draft";
import { runSaveErrorMessage } from "../lib/chatLimits";
import { criteriaAddError, criteriaUpdateError } from "../lib/criteriaError";
import { isAgentGone } from "../lib/loadError";
import { targetsCurrentAgent } from "../lib/messageTarget";

// The server owns everything except the in-progress run, which is a draft kept
// per agent in sessionStorage so a reload mid-conversation does not lose it.

function readDraft(agentId) {
  try {
    const raw = sessionStorage.getItem(draftKey(agentId));
    const parsed = raw === null ? [] : JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeDraft(agentId, messages) {
  try {
    if (messages.length === 0) sessionStorage.removeItem(draftKey(agentId));
    else sessionStorage.setItem(draftKey(agentId), JSON.stringify(messages));
  } catch {
    // Storage unavailable; the draft just lives in memory.
  }
}

function errorMessage(err) {
  return err instanceof Error && err.message ? err.message : "Something went wrong.";
}

// Shape that hooks report while nothing is loaded (no agent, or load pending).
const EMPTY = {
  agent: null,
  runCount: 0,
  transcripts: [],
  evalCriteria: [],
  patternSummary: null,
};

export function useSession(agentId) {
  // `loaded` records which agent the state below belongs to, so a stale or
  // not-yet-finished load is never mistaken for the current agent.
  const [loaded, setLoaded] = useState({ agentId: null, error: null, gone: false });
  // Bumped by retry() to re-run the load for the same agent.
  const [reloadKey, setReloadKey] = useState(0);
  const [agent, setAgent] = useState(EMPTY.agent);
  const [runCount, setRunCount] = useState(EMPTY.runCount);
  const [transcripts, setTranscripts] = useState(EMPTY.transcripts);
  const [currentTranscript, setCurrentTranscript] = useState([]);
  const [evalCriteria, setEvalCriteria] = useState(EMPTY.evalCriteria);
  const [patternState, setPatternStateRaw] = useState("idle");
  const [patternSummary, setPatternSummary] = useState(EMPTY.patternSummary);
  const [criteriaSaveError, setCriteriaSaveError] = useState(null);
  const [runSaveMessage, setRunSaveMessage] = useState(null);

  // Refs let async callbacks read the latest values after an await.
  const patternStateRef = useRef("idle");
  const criteriaRef = useRef([]);
  const draftRef = useRef([]);
  const agentIdRef = useRef(agentId);
  useEffect(() => {
    agentIdRef.current = agentId;
    // Cleared on unmount (and between agents) so a reply that lands later finds no agent.
    return () => {
      agentIdRef.current = null;
    };
  }, [agentId]);

  const setPatternState = useCallback((value) => {
    patternStateRef.current = value;
    setPatternStateRaw(value);
  }, []);

  const applyCriteria = useCallback((next) => {
    criteriaRef.current = next;
    setEvalCriteria(next);
  }, []);

  const applyDraft = useCallback((next) => {
    draftRef.current = next;
    setCurrentTranscript(next);
  }, []);

  useEffect(() => {
    if (agentId == null) return undefined;
    let cancelled = false;

    getAgentState(agentId)
      .then((state) => {
        if (cancelled) return;
        const criteria = state.criteria ?? [];
        setAgent(state.agent);
        setRunCount(state.run_count ?? 0);
        setTranscripts((state.runs ?? []).map((run) => run.messages));
        applyCriteria(criteria);
        setPatternSummary(state.pattern_summary ?? null);
        setPatternState(
          derivePatternState({
            patternSummary: state.pattern_summary,
            criteriaCount: criteria.length,
          })
        );
        applyDraft(readDraft(agentId));
        setCriteriaSaveError(null);
        setRunSaveMessage(null);
        setLoaded({ agentId, error: null, gone: false });
      })
      .catch((err) => {
        if (cancelled) return;
        const gone = isAgentGone(err);
        if (err instanceof ApiError && err.status === 401) {
          // The stored workspace is gone; forget it (and its agent) so the app does not loop.
          clearWorkspace();
        } else if (err instanceof ApiError && err.status === 404) {
          // Only the agent is gone; the workspace is still valid.
          clearAgentId();
        }
        setLoaded({ agentId, error: errorMessage(err), gone });
      });

    return () => {
      cancelled = true;
    };
  }, [agentId, reloadKey, applyCriteria, applyDraft, setPatternState]);

  const ready = agentId != null && loaded.agentId === agentId;
  const loadError = ready ? loaded.error : null;
  // True when the stored agent cannot come back (404/401); otherwise a retry may work.
  const loadErrorGone = ready && loaded.error !== null ? loaded.gone : false;

  // Show "Loading" again and re-run the load for the same agent.
  const retry = useCallback(() => {
    setLoaded({ agentId: null, error: null, gone: false });
    setReloadKey((n) => n + 1);
  }, []);
  const loading = agentId != null && !ready;

  const addMessage = useCallback(
    (message, forAgentId) => {
      // A late reply for an agent the user has since left is dropped.
      if (!targetsCurrentAgent(agentIdRef.current, forAgentId)) return;
      const next = [...draftRef.current, message];
      applyDraft(next);
      writeDraft(agentIdRef.current, next);
    },
    [applyDraft]
  );

  // Throws the in-progress run away without saving it.
  const discardDraft = useCallback(() => {
    const id = agentIdRef.current;
    if (id == null) return;
    applyDraft([]);
    writeDraft(id, []);
    setRunSaveMessage(null);
  }, [applyDraft]);

  // Posts the draft as a run; when the server says a pattern check is due,
  // runs it and moves the state machine accordingly.
  const saveRun = useCallback(async () => {
    const id = agentIdRef.current;
    if (id == null) return;
    const messages = draftRef.current;

    setRunSaveMessage(null);
    let saved;
    try {
      saved = await apiSaveRun(id, messages);
    } catch (err) {
      // Only the save failed: the pattern check state is unrelated, so leave it alone.
      if (agentIdRef.current === id) setRunSaveMessage(runSaveErrorMessage(err));
      return;
    }
    if (agentIdRef.current !== id) return;

    // Keep anything added to the draft while the save was in flight.
    const rest = remainingDraft(draftRef.current, messages.length);
    applyDraft(rest);
    writeDraft(id, rest);
    setTranscripts((prev) => [...prev, saved.run.messages]);
    setRunCount(saved.run_count);

    if (!saved.check_due) return;
    if (["checking", "generating"].includes(patternStateRef.current)) return;

    setPatternState("checking");
    try {
      const result = await apiCheckPattern(id);
      if (agentIdRef.current !== id) return;
      if (result.found) {
        setPatternSummary(result.pattern_summary);
        setPatternState("pattern_found");
      } else {
        setPatternState("pattern_not_found");
      }
    } catch {
      if (agentIdRef.current !== id) return;
      setPatternState("error");
    }
  }, [applyDraft, setPatternState]);

  // Rethrows on failure so the caller can show the message; state is set to "error" first.
  const discover = useCallback(async () => {
    const id = agentIdRef.current;
    if (id == null) return null;
    setPatternState("generating");
    try {
      // The response also carries the build job's id; the suite refresh resumes that job, and
      // the stored criterion (which Export all writes out) must stay a plain criterion.
      // eslint-disable-next-line no-unused-vars
      const { job_id, ...criterion } = await apiDiscoverCriterion(id);
      if (agentIdRef.current !== id) return null;
      applyCriteria([...criteriaRef.current, criterion]);
      setPatternSummary(null); // the server consumes the summary on discovery
      setPatternState("complete");
      return criterion;
    } catch (err) {
      if (agentIdRef.current === id) setPatternState("error");
      throw err instanceof Error ? err : new Error(errorMessage(err));
    }
  }, [applyCriteria, setPatternState]);

  const addCriteria = useCallback(
    async (fields) => {
      const id = agentIdRef.current;
      if (id == null) return null;
      setCriteriaSaveError(null);
      try {
        const criterion = await apiAddCriterion(id, fields);
        if (agentIdRef.current !== id) return null;
        applyCriteria([...criteriaRef.current, criterion]);
        return criterion;
      } catch (err) {
        setCriteriaSaveError(criteriaAddError(errorMessage(err)));
        return null;
      }
    },
    [applyCriteria]
  );

  // Optimistic: the edit shows immediately; if the server rejects it, the
  // criterion goes back to its last saved values and the error is surfaced.
  const updateCriteria = useCallback(
    async (id, fields) => {
      const current = criteriaRef.current.find((c) => c.id === id);
      if (!current) return;
      const previous = Object.fromEntries(Object.keys(fields).map((key) => [key, current[key]]));
      const unchanged = Object.keys(fields).every((key) => current[key] === fields[key]);
      if (unchanged) return;

      setCriteriaSaveError(null);
      applyCriteria(criteriaRef.current.map((c) => (c.id === id ? { ...c, ...fields } : c)));
      try {
        await apiUpdateCriterion(id, fields);
      } catch (err) {
        applyCriteria(criteriaRef.current.map((c) => (c.id === id ? { ...c, ...previous } : c)));
        setCriteriaSaveError(criteriaUpdateError(errorMessage(err)));
      }
    },
    [applyCriteria]
  );

  // Saves a manual prompt edit as a new prompt version and mirrors it on the agent. Rejects with
  // the server's error so the editor can show it; the draft and everything else stay as they are.
  const savePrompt = useCallback(async (text) => {
    const id = agentIdRef.current;
    if (id == null) return;
    const saved = await apiUpdatePrompt(id, text);
    if (agentIdRef.current !== id) return;
    setAgent((prev) =>
      prev
        ? {
            ...prev,
            system_prompt: saved.text,
            prompt_version_id: saved.version_id,
            prompt_version_number: saved.version_number,
          }
        : prev
    );
  }, []);

  // Reloads only the prompt fields of the agent (after a fix is promoted or a version restored)
  // so the prompt editor never shows stale text. The draft, criteria and pattern state are not
  // touched. A failed reload is ignored: the next load or edit brings the fields up to date.
  const refreshAgent = useCallback(async () => {
    const id = agentIdRef.current;
    if (id == null) return;
    let state;
    try {
      state = await getAgentState(id);
    } catch {
      return;
    }
    if (agentIdRef.current !== id || !state?.agent) return;
    const { system_prompt, prompt_version_id, prompt_version_number } = state.agent;
    setAgent((prev) =>
      prev && prev.id === id ? { ...prev, system_prompt, prompt_version_id, prompt_version_number } : prev
    );
  }, []);

  // Until the state for the current agent has loaded, expose nothing.
  const view = ready && !loadError
    ? { agent, runCount, transcripts, evalCriteria, patternSummary }
    : EMPTY;

  return {
    loading,
    loadError,
    loadErrorGone,
    retry,
    agent: view.agent,
    runCount: view.runCount,
    transcripts: view.transcripts,
    currentTranscript: ready && !loadError ? currentTranscript : EMPTY.transcripts,
    evalCriteria: view.evalCriteria,
    patternState: ready && !loadError ? patternState : "idle",
    patternSummary: view.patternSummary,
    criteriaSaveError,
    runSaveFailed: ready && !loadError && runSaveMessage !== null,
    runSaveMessage: ready && !loadError ? runSaveMessage : null,
    addMessage,
    saveRun,
    discardDraft,
    discover,
    addCriteria,
    updateCriteria,
    savePrompt,
    refreshAgent,
    setPatternState,
  };
}
