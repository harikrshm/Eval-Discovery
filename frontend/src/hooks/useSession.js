import { useState, useEffect, useCallback, useRef } from "react";
import {
  ApiError,
  getAgentState,
  saveRun as apiSaveRun,
  checkPattern as apiCheckPattern,
  discoverCriterion as apiDiscoverCriterion,
  addCriterion as apiAddCriterion,
  updateCriterion as apiUpdateCriterion,
} from "../api";
import { clearWorkspace } from "../lib/workspace";
import { derivePatternState } from "../lib/patternState";
import { remainingDraft } from "../lib/draft";

// The server owns everything except the in-progress run, which is a draft kept
// per agent in sessionStorage so a reload mid-conversation does not lose it.
const draftKey = (agentId) => `current_transcript:${agentId}`;

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
  const [loaded, setLoaded] = useState({ agentId: null, error: null });
  const [agent, setAgent] = useState(EMPTY.agent);
  const [runCount, setRunCount] = useState(EMPTY.runCount);
  const [transcripts, setTranscripts] = useState(EMPTY.transcripts);
  const [currentTranscript, setCurrentTranscript] = useState([]);
  const [evalCriteria, setEvalCriteria] = useState(EMPTY.evalCriteria);
  const [patternState, setPatternStateRaw] = useState("idle");
  const [patternSummary, setPatternSummary] = useState(EMPTY.patternSummary);
  const [criteriaSaveError, setCriteriaSaveError] = useState(null);

  // Refs let async callbacks read the latest values after an await.
  const patternStateRef = useRef("idle");
  const criteriaRef = useRef([]);
  const draftRef = useRef([]);
  const agentIdRef = useRef(agentId);
  useEffect(() => {
    agentIdRef.current = agentId;
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
        setLoaded({ agentId, error: null });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && (err.status === 401 || err.status === 404)) {
          // The stored workspace or agent is gone; forget it so the app does not loop.
          clearWorkspace();
        }
        setLoaded({ agentId, error: errorMessage(err) });
      });

    return () => {
      cancelled = true;
    };
  }, [agentId, applyCriteria, applyDraft, setPatternState]);

  const ready = agentId != null && loaded.agentId === agentId;
  const loadError = ready ? loaded.error : null;
  const loading = agentId != null && !ready;

  const addMessage = useCallback(
    (message) => {
      const next = [...draftRef.current, message];
      applyDraft(next);
      writeDraft(agentIdRef.current, next);
    },
    [applyDraft]
  );

  // Posts the draft as a run; when the server says a pattern check is due,
  // runs it and moves the state machine accordingly.
  const saveRun = useCallback(async () => {
    const id = agentIdRef.current;
    if (id == null) return;
    const messages = draftRef.current;

    let saved;
    try {
      saved = await apiSaveRun(id, messages);
    } catch {
      setPatternState("error");
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
      const criterion = await apiDiscoverCriterion(id);
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
        setCriteriaSaveError(errorMessage(err));
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
        setCriteriaSaveError(errorMessage(err));
      }
    },
    [applyCriteria]
  );

  // Until the state for the current agent has loaded, expose nothing.
  const view = ready && !loadError
    ? { agent, runCount, transcripts, evalCriteria, patternSummary }
    : EMPTY;

  return {
    loading,
    loadError,
    agent: view.agent,
    runCount: view.runCount,
    transcripts: view.transcripts,
    currentTranscript: ready && !loadError ? currentTranscript : EMPTY.transcripts,
    evalCriteria: view.evalCriteria,
    patternState: ready && !loadError ? patternState : "idle",
    patternSummary: view.patternSummary,
    criteriaSaveError,
    addMessage,
    saveRun,
    discover,
    addCriteria,
    updateCriteria,
    setPatternState,
  };
}
