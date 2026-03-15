import { useState, useCallback } from "react";
import { checkPattern as apiCheckPattern } from "../api";

const KEYS = {
  RUN_COUNT: "run_count",
  TRANSCRIPTS: "transcripts",
  CURRENT_TRANSCRIPT: "current_transcript",
  EVAL_CRITERIA: "eval_criteria",
  PATTERN_STATE: "pattern_state",
  PATTERN_SUMMARY: "pattern_summary",
  LAST_CHECKED_AT_RUN: "last_checked_at_run",
};

// Keywords covering all Alex/CloudSync behaviors from the system prompt.
// If any keyword appears in user messages across ≥2 runs, fire the LLM pattern check.
const PATTERN_KEYWORDS = [
  // Pricing guardrail (don't discuss pricing)
  "price", "pricing", "cost", "costs", "budget", "expensive", "cheap",
  "afford", "pay", "payment", "fee", "how much", "monthly", "annual",

  // Demo booking (primary goal)
  "demo", "meeting", "schedule", "calendar", "book", "appointment",
  "20 minutes", "30 minutes", "time slot",

  // Objection handling (one max, then let go)
  "not interested", "busy", "no thanks", "bad time", "pass",

  // Competitors (never badmouth)
  "competitor", "alternative", "already using", "other solution",
  "snowflake", "databricks",

  // Voicemail
  "voicemail", "leave a message",

  // Qualification
  "qualify", "decision maker", "data team", "pipeline",
];

function getItem(key, fallback) {
  try {
    const raw = sessionStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function setItem(key, value) {
  sessionStorage.setItem(key, JSON.stringify(value));
}

/**
 * Returns true if at least one PATTERN_KEYWORD appears in user messages
 * across ≥2 different transcripts.
 */
function hasKeywordOverlap(transcripts) {
  if (transcripts.length < 2) return false;
  const runKeywords = transcripts.map((transcript) =>
    new Set(
      transcript
        .filter((m) => m.role === "user")
        .flatMap((m) =>
          PATTERN_KEYWORDS.filter((kw) =>
            m.content.toLowerCase().includes(kw)
          )
        )
    )
  );
  return PATTERN_KEYWORDS.some(
    (kw) => runKeywords.filter((s) => s.has(kw)).length >= 2
  );
}

export function useSession() {
  const [runCount, setRunCountState] = useState(() => getItem(KEYS.RUN_COUNT, 0));
  const [transcripts, setTranscriptsState] = useState(() => getItem(KEYS.TRANSCRIPTS, []));
  const [currentTranscript, setCurrentTranscriptState] = useState(() => getItem(KEYS.CURRENT_TRANSCRIPT, []));
  const [evalCriteria, setEvalCriteriaState] = useState(() => getItem(KEYS.EVAL_CRITERIA, []));
  const [patternState, setPatternStateRaw] = useState(() => getItem(KEYS.PATTERN_STATE, "idle"));
  const [patternSummary, setPatternSummaryRaw] = useState(() => getItem(KEYS.PATTERN_SUMMARY, null));
  const [lastCheckedAtRun, setLastCheckedAtRunState] = useState(() => getItem(KEYS.LAST_CHECKED_AT_RUN, 0));

  const setPatternState = useCallback((val) => {
    setPatternStateRaw(val);
    setItem(KEYS.PATTERN_STATE, val);
  }, []);

  const setPatternSummary = useCallback((val) => {
    setPatternSummaryRaw(val);
    setItem(KEYS.PATTERN_SUMMARY, val);
  }, []);

  const setLastCheckedAtRun = useCallback((val) => {
    setLastCheckedAtRunState(val);
    setItem(KEYS.LAST_CHECKED_AT_RUN, val);
  }, []);

  const addMessage = useCallback((message) => {
    setCurrentTranscriptState((prev) => {
      const next = [...prev, message];
      setItem(KEYS.CURRENT_TRANSCRIPT, next);
      return next;
    });
  }, []);

  /**
   * Appends a new criterion to the evalCriteria array and persists to sessionStorage.
   * Called from App.jsx handleDiscoverEvals after a successful /generate-criteria response.
   */
  const addCriteria = useCallback((criterion) => {
    setEvalCriteriaState((prev) => {
      const next = [...prev, criterion];
      setItem(KEYS.EVAL_CRITERIA, next);
      return next;
    });
  }, []);

  /**
   * Updates a specific criterion by id. Called on blur from EvalCriteriaPanel field edits.
   */
  const updateCriteria = useCallback((id, fields) => {
    setEvalCriteriaState((prev) => {
      const next = prev.map((c) => (c.id === id ? { ...c, ...fields } : c));
      setItem(KEYS.EVAL_CRITERIA, next);
      return next;
    });
  }, []);

  /**
   * Saves the current transcript as a completed run, then evaluates whether to fire
   * the LLM pattern check.
   *
   * Pattern check fires if:
   *   1. nextCount - lastCheckedAtRun >= 2  (checks every 2 runs)
   *   2. patternState NOT IN ["checking", "generating"]
   *   3. hasKeywordOverlap detects keyword match across ≥2 transcripts
   *
   * On success (found=true): updates lastCheckedAtRun, sets patternSummary, → "pattern_found"
   * On success (found=false): updates lastCheckedAtRun → "pattern_not_found"
   * On error: does NOT update lastCheckedAtRun → "error" (allows retry on next run)
   */
  const saveRun = useCallback(async (currentState) => {
    const {
      currentPatternState,
      currentTranscripts,
      currentRunCount,
      currentCurrentTranscript,
      currentLastCheckedAtRun,
    } = currentState;

    const nextTranscripts = [...currentTranscripts, currentCurrentTranscript];
    const nextCount = currentRunCount + 1;

    setTranscriptsState(nextTranscripts);
    setRunCountState(nextCount);
    setItem(KEYS.TRANSCRIPTS, nextTranscripts);
    setItem(KEYS.RUN_COUNT, nextCount);
    setCurrentTranscriptState([]);
    setItem(KEYS.CURRENT_TRANSCRIPT, []);

    if (
      nextCount - currentLastCheckedAtRun >= 2 &&
      !["checking", "generating"].includes(currentPatternState) &&
      hasKeywordOverlap(nextTranscripts)
    ) {
      setPatternState("checking");
      try {
        const apiTranscripts = nextTranscripts.map((t) =>
          t.map((m) => ({ role: m.role, content: m.content }))
        );
        const result = await apiCheckPattern(apiTranscripts);
        setLastCheckedAtRun(nextCount);
        if (result.found) {
          setPatternSummary(result.pattern_summary); // overwrites any previous summary
          setPatternState("pattern_found");
        } else {
          setPatternState("pattern_not_found");
        }
      } catch {
        setPatternState("error");
        // lastCheckedAtRun NOT updated — allows retry on next qualifying run
      }
    }
  }, [setPatternState, setPatternSummary, setLastCheckedAtRun]);

  return {
    runCount,
    transcripts,
    currentTranscript,
    evalCriteria,
    patternState,
    patternSummary,
    lastCheckedAtRun,   // REQUIRED: App.jsx passes this as currentLastCheckedAtRun to saveRun
    addMessage,
    saveRun,
    addCriteria,
    updateCriteria,
    setPatternState,
  };
}
