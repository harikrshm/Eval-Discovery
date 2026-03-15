import { useState, useCallback } from "react";

const KEYS = {
  RUN_COUNT: "run_count",
  TRANSCRIPTS: "transcripts",
  CURRENT_TRANSCRIPT: "current_transcript",
  EVAL_TRIGGERED: "eval_triggered",
  EVAL_DISMISSED: "eval_dismissed",
  EVAL_RUBRIC: "eval_rubric",
  EVAL_INTENT: "eval_intent",
};

const EVAL_TRIGGER_THRESHOLD = 5;

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

export function useSession() {
  const [runCount, setRunCountState] = useState(() => getItem(KEYS.RUN_COUNT, 0));
  const [transcripts, setTranscriptsState] = useState(() => getItem(KEYS.TRANSCRIPTS, []));
  const [currentTranscript, setCurrentTranscriptState] = useState(() => getItem(KEYS.CURRENT_TRANSCRIPT, []));
  const [evalTriggered, setEvalTriggeredState] = useState(() => getItem(KEYS.EVAL_TRIGGERED, false));
  const [evalDismissed, setEvalDismissedState] = useState(() => getItem(KEYS.EVAL_DISMISSED, false));
  const [evalRubric, setEvalRubricState] = useState(() => getItem(KEYS.EVAL_RUBRIC, null));
  const [evalIntent, setEvalIntentState] = useState(() => getItem(KEYS.EVAL_INTENT, null));

  const addMessage = useCallback((message) => {
    setCurrentTranscriptState((prev) => {
      const next = [...prev, message];
      setItem(KEYS.CURRENT_TRANSCRIPT, next);
      return next;
    });
  }, []);

  const saveRun = useCallback(() => {
    const nextTranscripts = [...transcripts, currentTranscript];
    const nextCount = runCount + 1;
    setTranscriptsState(nextTranscripts);
    setRunCountState(nextCount);
    setItem(KEYS.TRANSCRIPTS, nextTranscripts);
    setItem(KEYS.RUN_COUNT, nextCount);
    setCurrentTranscriptState([]);
    setItem(KEYS.CURRENT_TRANSCRIPT, []);
  }, [transcripts, currentTranscript, runCount]);

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

  const saveIntent = useCallback((intent) => {
    setEvalIntentState(intent);
    setItem(KEYS.EVAL_INTENT, intent);
  }, []);

  const shouldTriggerAnalysis = runCount >= EVAL_TRIGGER_THRESHOLD && !evalTriggered;

  return {
    runCount,
    transcripts,
    currentTranscript,
    evalTriggered,
    evalDismissed,
    evalRubric,
    evalIntent,
    shouldTriggerAnalysis,
    threshold: EVAL_TRIGGER_THRESHOLD,
    addMessage,
    saveRun,
    markTriggered,
    dismissEval,
    saveRubric,
    saveIntent,
  };
}
