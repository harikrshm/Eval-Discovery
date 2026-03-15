import { useState, useCallback } from "react";
import { checkPattern as apiCheckPattern } from "../api";

const KEYS = {
  RUN_COUNT: "run_count",
  TRANSCRIPTS: "transcripts",
  CURRENT_TRANSCRIPT: "current_transcript",
  EVAL_TRIGGERED: "eval_triggered",
  EVAL_DISMISSED: "eval_dismissed",
  EVAL_RUBRIC: "eval_rubric",
  PATTERN_STATE: "pattern_state",
  PATTERN_SUMMARY: "pattern_summary",
};

// Keywords that signal the user is probing a specific Alex behavior.
// If any keyword appears in user messages across ≥2 runs, fire the LLM pattern check.
const PATTERN_KEYWORDS = [
  "price", "pricing", "cost", "budget", "demo", "meeting",
  "voicemail", "not interested", "competitor", "qualify",
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
  const [evalTriggered, setEvalTriggeredState] = useState(() => getItem(KEYS.EVAL_TRIGGERED, false));
  const [evalDismissed, setEvalDismissedState] = useState(() => getItem(KEYS.EVAL_DISMISSED, false));
  const [evalRubric, setEvalRubricState] = useState(() => getItem(KEYS.EVAL_RUBRIC, null));
  const [patternState, setPatternStateRaw] = useState(() => getItem(KEYS.PATTERN_STATE, "idle"));
  const [patternSummary, setPatternSummaryRaw] = useState(() => getItem(KEYS.PATTERN_SUMMARY, null));

  function setPatternState(val) {
    setPatternStateRaw(val);
    setItem(KEYS.PATTERN_STATE, val);
  }

  function setPatternSummary(val) {
    setPatternSummaryRaw(val);
    setItem(KEYS.PATTERN_SUMMARY, val);
  }

  const addMessage = useCallback((message) => {
    setCurrentTranscriptState((prev) => {
      const next = [...prev, message];
      setItem(KEYS.CURRENT_TRANSCRIPT, next);
      return next;
    });
  }, []);

  const markTriggered = useCallback(() => {
    setEvalTriggeredState(true);
    setItem(KEYS.EVAL_TRIGGERED, true);
  }, []);

  const dismissEval = useCallback(() => {
    setEvalDismissedState(true);
    setItem(KEYS.EVAL_DISMISSED, true);
  }, []);

  const saveRubric = useCallback((rubric) => {
    setEvalRubricState(rubric);
    setItem(KEYS.EVAL_RUBRIC, rubric);
  }, []);

  /**
   * Saves the current transcript as a completed run, then immediately evaluates
   * whether to fire the LLM pattern check.
   *
   * Pattern check fires if:
   *   1. runCount after save >= 2
   *   2. patternState is "idle", "pattern_not_found", or "error" (not checking/found/generating/complete)
   *   3. The heuristic detects keyword overlap across ≥2 transcripts
   */
  const saveRun = useCallback(async (currentState) => {
    // currentState is passed from App to avoid stale closure issues
    const { currentPatternState, currentTranscripts, currentRunCount, currentCurrentTranscript } = currentState;

    const nextTranscripts = [...currentTranscripts, currentCurrentTranscript];
    const nextCount = currentRunCount + 1;

    setTranscriptsState(nextTranscripts);
    setRunCountState(nextCount);
    setItem(KEYS.TRANSCRIPTS, nextTranscripts);
    setItem(KEYS.RUN_COUNT, nextCount);
    setCurrentTranscriptState([]);
    setItem(KEYS.CURRENT_TRANSCRIPT, []);

    const retriggerable = ["idle", "pattern_not_found", "error"];
    if (
      nextCount >= 2 &&
      retriggerable.includes(currentPatternState) &&
      hasKeywordOverlap(nextTranscripts)
    ) {
      setPatternState("checking");
      try {
        const apiTranscripts = nextTranscripts.map((t) =>
          t.map((m) => ({ role: m.role, content: m.content }))
        );
        const result = await apiCheckPattern(apiTranscripts);
        if (result.found) {
          setPatternSummary(result.pattern_summary);
          setPatternState("pattern_found");
        } else {
          setPatternState("pattern_not_found");
        }
      } catch {
        setPatternState("error");
      }
    }
  }, []);

  return {
    runCount,
    transcripts,
    currentTranscript,
    evalTriggered,
    evalDismissed,
    evalRubric,
    patternState,
    patternSummary,
    addMessage,
    saveRun,
    markTriggered,
    dismissEval,
    saveRubric,
    setPatternState,
  };
}
