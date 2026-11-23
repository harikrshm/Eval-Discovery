import { useState, useEffect, useRef } from "react";
import { exportSuite } from "../api";
import { useSession } from "./useSession";
import { useTests } from "./useTests";

// The improvement loop for one agent: the session (agent, runs, criteria, pattern state), the test
// suite, and the handlers that tie them together. The chat app and the Assist console both wire
// the criteria panel through this hook, so the two screens behave the same.
//
// `onOpenPanel` is called when an action should bring the criteria panel into view (the app's
// drawer opens; the console's panel is always visible and passes nothing).

function downloadJson(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  try {
    a.click();
  } finally {
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }
}

export function useEvalLoop(agentId, { onOpenPanel } = {}) {
  const session = useSession(agentId);
  const tests = useTests(agentId);
  const {
    agent,
    evalCriteria,
    patternState,
    patternSummary,
    criteriaSaveError,
    discover,
    addCriteria,
    updateCriteria,
    savePrompt,
    refreshAgent,
  } = session;

  const [criteriaError, setCriteriaError] = useState(null);

  // After a promotion or a restore the suite is on a newer prompt version than the agent the chat
  // screen loaded. Reload the agent's prompt fields once per difference; a failed reload is not
  // retried until the suite reports another version (the editor also reloads before it opens).
  const suiteVersionId = tests.suite?.prompt_version_id ?? null;
  const agentVersionId = agent?.prompt_version_id ?? null;
  const agentRefreshing = useRef(false);
  const agentRefreshFailedFor = useRef(null);
  useEffect(() => {
    if (!suiteVersionId || !agentVersionId || suiteVersionId === agentVersionId) {
      agentRefreshFailedFor.current = null;
      return;
    }
    if (agentRefreshing.current || agentRefreshFailedFor.current === suiteVersionId) return;
    agentRefreshing.current = true;
    refreshAgent().then((ok) => {
      agentRefreshing.current = false;
      if (!ok) agentRefreshFailedFor.current = suiteVersionId;
    });
  }, [suiteVersionId, agentVersionId, refreshAgent]);

  // Reset criteriaError whenever a new pattern check starts. This adjusts state during render,
  // keyed on the previous pattern state, rather than syncing it in an effect.
  const [seenPatternState, setSeenPatternState] = useState(patternState);
  if (seenPatternState !== patternState) {
    setSeenPatternState(patternState);
    if (patternState === "checking") setCriteriaError(null);
  }

  // `requireSummary: false` lets a caller that has just seen a pattern found (the console's check
  // button) discover without waiting for the session to learn the summary; the server still
  // refuses (409) when there is none.
  // Resolves true when a criterion was discovered, false when nothing was (the error is in
  // `criteriaError`).
  async function discoverEvals({ requireSummary = true } = {}) {
    if (requireSummary && !patternSummary) return false;
    setCriteriaError(null);
    try {
      await discover();
      // The server starts building tests for the new criterion; the refresh picks that job up.
      tests.refresh();
      onOpenPanel?.();
      return true;
    } catch (err) {
      setCriteriaError(err.message);
      return false;
    }
  }

  function exportAll() {
    downloadJson(evalCriteria, "eval-criteria.json");
  }

  // A failure rejects, so the panel can show the server's message beside the button.
  async function exportTestSuite() {
    downloadJson(await exportSuite(agentId), "eval-suite.json");
  }

  async function addCriterion() {
    onOpenPanel?.();
    const created = await addCriteria({
      name: "New criterion",
      definition: "",
      good_example: "",
      bad_example: "",
    });
    // The suite learns about the new criterion so it can offer "Build tests".
    if (created) tests.refresh();
  }

  async function updateCriterion(id, fields) {
    await updateCriteria(id, fields);
    // Editing what a criterion means makes its test results out of date.
    if ("definition" in fields || "good_example" in fields || "bad_example" in fields) {
      tests.refresh();
    }
  }

  async function savePromptText(text, baseVersionId) {
    try {
      await savePrompt(text, baseVersionId);
    } catch (err) {
      // A refusal because the prompt moved on (or any failure): learn the live version so the
      // editor can flag the draft as based on an older one.
      refreshAgent();
      throw err;
    }
    tests.refresh();
  }

  // A restore changes the live prompt: load it into the chat screen too. A failed reload never
  // hides the restore (the editor flags a draft based on the old version by itself).
  async function restoreVersion(versionId) {
    const ok = await tests.restore(versionId);
    if (ok) await refreshAgent();
    return ok;
  }

  // Everything EvalCriteriaPanel needs except what the screen decides itself: how it is dismissed
  // and whether a discovery is pending.
  const panelProps = {
    evalCriteria,
    patternState,
    onUpdate: updateCriterion,
    onAdd: addCriterion,
    saveError: criteriaSaveError,
    onExportAll: exportAll,
    onExportSuite: exportTestSuite,
    suite: tests.suite,
    jobs: tests.jobs,
    testsError: tests.error,
    testsLoading: tests.loading,
    onRetryTests: tests.refresh,
    agentName: agent?.name,
    userRoleLabel: agent?.user_role_label,
    onReplay: tests.replay,
    onBuildTests: tests.buildTests,
    onRunAll: tests.runAll,
    onSuggestFix: tests.suggestFix,
    onGenerateHeldout: tests.generateHeldout,
    onApprove: tests.approve,
    onReject: tests.reject,
    versionHistory: { agentId, onRestore: restoreVersion },
    promptEditor: {
      promptText: agent?.system_prompt,
      versionNumber: agent?.prompt_version_number,
      versionId: agent?.prompt_version_id,
      verifyingNumber:
        tests.suite?.candidate?.status === "approved_verifying" ? tests.suite.candidate.number : null,
      onSave: savePromptText,
      onBeforeEdit: refreshAgent,
    },
  };

  return { session, tests, criteriaError, setCriteriaError, discoverEvals, panelProps };
}
