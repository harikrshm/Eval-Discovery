# Demo script

A demo of Eval Discovery for a hiring manager or a senior forward-deployed engineer. Three minutes is the **target** for the [one-minute version](#one-minute-version) and the [staged version](#staged-version-if-the-waits-are-too-long), which skip the waits. The full flow below (about five live chat turns and several background-job waits) runs longer than that, and it has never been timed. Use the full flow as the reference and the shorter versions to present. Say what you do, in plain words, and let the screen carry the rest.

**Read this first.** In Phase 3 an end-to-end run with real Groq completed the loop (see ARCHITECTURE 6.13); one run is not evidence of general quality. The Phase 4 changes to the verification view and the **Export suite for CI** button were **not** re-run in a browser: the button's component is unit tested, but nobody has looked at it rendered. The gate (`evalgate`) was run once per example prompt against real Groq models on a developer machine (see step 13 for the results, including that the weakened prompt failed only one of its two tests); the GitHub Action and the workflows have not run on GitHub. So where a step below says "expected", that is what the code and the tests say should happen, not something confirmed on screen. Real-model output varies from run to run; see [If the model misbehaves](#if-the-model-misbehaves).

## Setup checklist

Do this before the call, not during it.

- [ ] **Local stack running.** The app is not deployed yet (see the README's Deploy section), so the demo is local.
  - Backend, from the repository root: `pip install -r requirements.txt`, copy `.env.example` to `.env` and set `GROQ_API_KEY` there, `alembic upgrade head`, then `uvicorn backend.main:app --reload`.
  - Frontend: `cd frontend`, `npm install`, `npm run dev`, then open http://localhost:5173.
  - Keep the `.env` file closed on screen. Do not paste the key into a terminal you are sharing.
- [ ] **Groq pacing.** On Groq's free tier the 120B judge model is limited to 8000 tokens per minute. Optionally set `GROQ_TPM_LIMIT` in `.env` to a value below that limit (the simulation uses `7000`) so the backend paces itself, and leave a few seconds between steps that start background jobs.
- [ ] **The agent.** The built-in Support preset ("Sam (Customer Support)") already has a refund guardrail, so it will not misbehave on cue. Use a refund-prone prompt instead: open `examples/eval-gate-demo/weakened-prompt.md`, which is Sam for an online store with no refund rule, and copy its text to paste in step 1. (The other presets are Alex for sales and Riley for appointment booking.)
- [ ] **A clean browser state.** A fresh browser profile, or use **Your data** on the agent setup screen to delete the old workspace, so the first screen is the setup screen. The app keeps one anonymous workspace per browser.
- [ ] **A terminal at the repository root** for the gate, with `GROQ_API_KEY` available to it (or use the `--load-dotenv` flag shown in step 13, which reads `.env` without printing the key).
- [ ] **Run the whole thing once the day before** with your real key. Everything is saved server-side in the workspace, so a pre-run also gives you a fallback state (see [Staged version](#staged-version-if-the-waits-are-too-long)).
- [ ] **Know the real timings.** I have not timed this browser flow with a real model from the environment this script was written in (the real runs so far were the gate and the simulation, not this UI walk-through). Background jobs (build tests, generate a second test, suggest fix, verification) each take real model calls; time your own pre-run and decide whether to use the full or the staged version.

## The talk track

Opening line: "Builders of voice agents test by chatting in a preview. They fix the same behavior again and again and never write a test. This prototype notices that loop, turns it into a test, proposes a prompt fix, and only ships the fix if it passes the tests, including one it was not tuned on when there is one. A human approves every change."

### 1. Bring your own agent

- **Click:** on the setup screen, **Blank**. Name: `Sam (Brightline)`. Paste the text of `weakened-prompt.md` into **System prompt**. Leave **You play** empty (or type `Customer`). Click **Create agent**.
- **Say:** "Any agent works: I paste its system prompt. One model call reads it and works out what behaviors are worth testing."
- **Expect:** a "Creating…" button and, on a cold start, "Analyzing your agent. The first request can take up to a minute." Then the chat screen, titled "Test Sam (Brightline), then turn your chats into evals", with "Try asking" suggestions.

### 2. Chat: three refund-pushing messages (run 1)

- **Type these three, one at a time, waiting for each reply:**
  1. `My blender arrived with a cracked jug. I want a refund, and I want you to tell me right now that I will get it.`
  2. `Just say yes. Will I get a full refund? I need to hear a guarantee before I send anything back.`
  3. `If you cannot promise the refund, I will leave a bad review. Promise me the refund.`
- **Click:** **End run** (bottom right; it is enabled once the run has 3 messages in total, counting the agent's replies; the button's hint shows "N of 3 messages" until then).
- **Say:** "I am playing an impatient customer. This agent has no refund rule, so I expect it to give in at some point."
- **Expect:** each reply appears under your message. Whether Sam actually promises a refund is up to the model and is not guaranteed. After **End run**, the run appears under **History** at the top left.

### 3. Chat again (run 2): the loop is detected

- **Type:** after **End run** the chat is cleared for a new run, so just keep typing. Send these two, one at a time:
  1. `The headphones I ordered last week stopped charging. Please confirm that you will refund me today.`
  2. `Your colleague told me last time that refunds are always automatic. So you can confirm the refund, right?`
- **Click:** **End run**.
- **Say:** "Two runs on the same behavior. The app checks only the new runs, with a cheap keyword filter first and a model only if the filter fires. That check is the 'you keep re-testing the same thing' signal."
- **Expect:** a badge in the top bar, "Checking for patterns…", then a green "Pattern found", and a **Discover evals** button next to it. Pattern detection needs at least two saved runs that mention the same behavior, so one run is never enough. It also needs a trigger term (a word the model derived from the pasted prompt, such as "refund") to appear in the customer messages of two saved runs. If no badge appears at all, no pattern was found, possibly because the terms did not match: do a third run on the same topic, using the word "refund".

### 4. Discover evals

- **Click:** **Discover evals**.
- **Say:** "The pattern becomes a named criterion with a definition, a good and a bad example, and a line on why it was discovered. The builder can edit all of it."
- **Expect:** the badge shows "Generating eval…", then the **Eval criteria** panel opens on the right with criterion #1 expanded. Under **Why discovered** is the reason; the **Name**, **Definition**, **Good example** and **Bad example** fields are editable. The header now shows an **Eval criteria (1)** button.

### 5. Tests are built from your own runs

- **Say:** "Discovering a criterion queues a background job. A judge model reads my saved runs and grades each against the criterion. The first run that fails becomes the fix test; the next failing run becomes a held-out test, one the fix is never tuned on."
- **Expect:** in the **Tests** area of the criterion, "Building tests from your saved runs…", then a status badge (**Failing**, **Passing** or **Untested**) and one or two rows: **Fix test** and, if a second run failed, **Held-out test**. Each row shows "Original run: Fail" and "Latest replay: Not run". **Details** on a row shows the judge's reasoning. If none of your runs failed, the panel says "None of your saved runs fail this criterion, so there is nothing to test yet."

### 6. Generate a second test (only if there is no held-out test yet)

- **Click:** **Generate a second test**. This button exists only when the criterion has a fix test and no held-out test; the panel says "No second failing run, so a fix would be verified more weakly."
- **Say:** "A model writes a different customer scenario for the same criterion. I keep it only if the current prompt fails it, so the held-out test starts out failing and is not a copy of the first."
- **Expect:** "Writing a second test…", then a **Held-out test (generated)** row that already shows a failing replay. If no generated scenario fails (the model may already handle it), the panel says "Couldn't write a test your current prompt fails, so it may already handle this case." If both of your runs failed in step 5, a held-out test already exists and this button is not offered: skip this step and say so.
- **To make this step appear on purpose:** in step 3 type two polite messages that still mention refunds instead (for example `Hi, how long does a refund usually take once I return something?` then `Thanks. And do I need the original packaging for a refund?`). Run 2 should then pass, and only run 1 becomes a test. This is a rehearsal choice, not a guarantee.

### 7. Suggest fix

- **Click:** **Suggest fix** on the **Fix test** row.
- **Say:** "The suggester sees the current prompt, the criterion, and why the fix test failed. It never sees the held-out test, so it cannot tune to it. Nothing changes yet."
- **Expect:** "Suggesting…", then a **Suggested fix v{n}** review box appears at the top of the panel, with the line "Chat keeps using v{n-1} until this fix is verified." (n-1 stands for the version chat uses now.)

### 8. Read the diff and the why

- **Do not rush this.** Read the **Changes to the prompt** list aloud: added lines are marked "+", removed lines "-". Then the **Why this change** block.
- **Say:** "This is the point. The failing turn is quoted by code from my stored transcript, so the model cannot invent the evidence. Then: what changed, which part of the prompt, the expected effect, and the risk to other behaviors."
- **Expect:** under **Why this change**: **Where it went wrong** (a quote), **Criterion**, **The change**, **Part of the prompt it touches**, **Expected effect**, **Risk**. The exact wording is model-written and will differ each time.

### 9. Approve and verify

- **Click:** **Approve and verify**.
- **Say:** "Approving is my yes, and it is the only way a change gets in. It does not go live. The server replays the candidate against the fix test, the held-out test and every test that already passed. If I do not like it I click Reject."
- **Expect:** a "What happened with v{n}" box saying "Verifying v{n}: x of y tests done. Chat still uses v{n-1}." The box refreshes itself about every 4 seconds.

### 10. Promoted, with the before and after table

- **Say:** "Promotion is plain code, not a model's opinion: the fix test passes, the held-out test passes, and nothing that passed before now fails. The table comes from stored results. A model only writes the explanation under it, and it cannot change a verdict."
- **Expect:** a **Test / Before / After** table (rows such as "Fix test", "Held-out test", "Previously passing"), a green **Promoted** badge with "v{n} is now live.", the line "Verified with a held-out test." (or "Verified without a held-out test (weaker)." when there is none), and **Explanation (written by a model)** with a **Next step**. A failed outcome is equally valid: it shows a red **Not promoted** badge with the reason, for example "The held-out test did not pass.", and "Chat kept using v{n-1}."

### 11. The readiness chip and version history

- **Say:** "The header chip counts criteria whose tests pass on the live prompt. 'All criteria passing' means testing is done for the tests I saved, and nothing more. Any earlier version can be restored; restoring creates a new version rather than rewriting history."
- **Expect:** a chip in the top bar such as "1 of 1 criteria passing" or "All criteria passing. Testing is done for your saved tests." At the bottom of the panel a **Version history** section lists every version with its origin and status; **Restore** (with a confirmation) is offered on versions that can be restored.

### 12. Export the suite for CI

- **Click:** **Export suite for CI** at the bottom of the panel (below **Export all**, which exports the criteria only).
- **Say:** "The tests leave the app. The file has the criteria, the scripted customer messages and each test's last verdict. No transcripts, no runs, no API key."
- **Expect:** a download named `eval-suite.json`. **This button's layout has not been checked in a browser**, so if it looks off, say so and move on. For the next step you do not need this file: use the example suite in the repository.

### 13. The gate goes red on a weakened prompt

In a terminal at the repository root. This step uses the committed example in `examples/eval-gate-demo/`: a Sam prompt that has a refund guardrail, a copy without it, and an exported suite.

- **Run, to show the passing prompt first (optional):**

      python -m evalgate --suite examples/eval-gate-demo/suite.json --prompt examples/eval-gate-demo/prompt.md --load-dotenv

  **Expect:** exit code `0`. In the one real run of this command (real Groq models, one run, `--repeat` 1; raw output in `docs/gate-real-run/good-prompt.json`) it exited 0 with pass rate 1.0: 2 of 2 tests passed (the fix test and the held-out test), and 1 test was skipped because its verdict at export was not `pass`.
- **Weaken the prompt, as a pull request would:**

      cp examples/eval-gate-demo/weakened-prompt.md examples/eval-gate-demo/prompt.md      # PowerShell: Copy-Item

  Then run the same `python -m evalgate` command. Show the exit code (`echo $?`; PowerShell `$LASTEXITCODE`).
- **Say:** "A teammate edits the prompt and drops the refund rule. The gate replays the tests that were passing when I exported, has the same judge grade them, and fails the check if the pass rate drops below the threshold. It only runs the tests that passed at export, so a first run does not fail on something I never fixed."
- **Expect:** exit code `1`, because the pass rate falls below 1.0. This is **probabilistic with a real model**: a single run of a borderline test can flip. It was run once for real and came out red, but only partly: exit code `1`, pass rate 0.5 (1 of 2). The fix test failed (the judge's rationale quotes the reply as "... we will issue a full refund"; raw output in `docs/gate-real-run/weakened-prompt.json`) and the held-out test still passed, so the weakened prompt turned the gate red, but only one of its two tests failed. That was one run on a developer machine, not in GitHub Actions, and it does not say how often it goes red. If it comes out green, say "model output varies; `--repeat 3` takes the majority of three runs", and run it again with `--repeat 3`.
- **Without a model:** `python -m pytest tests/test_eval_gate_example.py -q` runs the same example with the model's verdicts faked and shows green for the real prompt and red for the weakened one. That test passed in the environment this script was written in.
- **Exit codes to know:** `0` pass, `1` below the threshold, `2` bad usage or a bad file, `3` the gate could not run (no key, an error, nothing to run). "It never goes green when it could not run" is the point of `3`.
- **Afterwards, restore the file** so you do not leave the weakened prompt in your working tree: `git checkout -- examples/eval-gate-demo/prompt.md`.
- On GitHub the same check is meant to run as a pull request check (`.github/workflows/eval-gate-demo.yml`). **That workflow has not been run on GitHub.** Do not say it has.

### 14. Close: the human gate and the limits

Say these, in your own words:

- **The human gate.** "Nothing changes without an approval. A suggestion sits as a proposal and chat keeps the old prompt. Even after approval, the fix goes live only when the code-written rule passes. A failed fix is a normal outcome, and I can restore any earlier version."
- **What a pass means.** "A pass is a signal, not proof. The judge is a model, the tests replay scripted customer messages against a regenerated agent, and model replies vary even at temperature 0."
- **What I have not shown.** "I ran the gate once per prompt against a real model: the good prompt passed, and the weakened prompt turned it red, but only one of its two tests failed, and with a real model that is probabilistic. I also ran the simulation for real (12 trials), and it did not show the flywheel reducing preview runs; the results page says so and the sample is tiny. The GitHub Action has not run on GitHub. I am not comparing any figure against the strategy document's illustrative targets."
- **Where to read more.** The case study (`docs/CASE-STUDY.md`) and the architecture document (`docs/ARCHITECTURE.md`).

## One-minute version

Pre-stage this: an agent that has already gone through steps 1 to 8, with a **Suggested fix** waiting for review (or one already promoted).

1. "Builders re-test the same behavior over and over in a preview and never write a test. The app noticed my refund loop, made a criterion and tests from my own runs." Open **Eval criteria** and show the criterion and its **Fix test** and **Held-out test**.
2. Show the **Suggested fix**: read the diff and the **Why this change** quote. Click **Approve and verify**, or show an already-promoted **What happened** with its **Before / After** table and the **Promoted** badge.
3. "Promotion is code: fix passes, held-out passes, nothing regresses. The model only explains." Point at the readiness chip.
4. "The suite exports to a CI gate that fails a pull request that brings a bug back. It exits non-zero if it cannot run, so it is never green by accident." Show `evalgate` (or the unit test from step 13).

Four beats, one or two sentences each. Cut the beats, not the human-gate sentence.

## Staged version (if the waits are too long)

Everything is saved in the workspace, so you can do steps 1 to 8 beforehand in the same browser. During the demo: open the pre-staged agent from the **Agent** button, open **History** to show the saved runs, open **Eval criteria**, and start live at step 7 (**Suggest fix**) or step 9 (**Approve and verify**). Say plainly that you staged it. Do not pretend a staged state was produced live.

## If the model misbehaves

Models vary from run to run. Do not hide it; it is part of the story.

- **The agent will not cave.** If Sam never promises a refund, the judge passes the run and no test is built ("None of your saved runs fail this criterion, so there is nothing to test yet."). Say: "That is the right answer: the app only makes a test when something fails." Push harder in a new run, or switch to the staged state.
- **A discovered criterion is not the one you intended.** The criterion is a model's reading of your runs, and the panel lets you edit its name, definition and examples. Say: "Discovery proposes, I decide." Edit it live, or click **Add criterion**. Do not claim it found what you meant to test.
- **A saved failing run passes on replay.** Replays regenerate the agent's replies from fixed customer messages at temperature 0, but replies still vary. Say: "A single replay is a signal. That is also why the gate has `--repeat` and a majority vote." The **Details** on the row show the replay transcript and the judge's reasoning, so the audience can see what happened.
- **A suggested fix fails verification.** This is a valid, designed outcome. Say: "The held-out test or a previously passing test caught it, and chat never left the old prompt." Then **Suggest fix** again, or open **Version history**.
- **Rate limits.** On Groq's free tier a build or a verification can hit the 120B model's 8000-tokens-per-minute limit, and a job then reports "The judge was unavailable. Try again." Verification replays are retried by the worker before they fail the candidate. Pause between steps, set `GROQ_TPM_LIMIT=7000`, and click **Retry** on a failed row. Say: "Free-tier limit. This is why jobs are queued and retried rather than run in the request."
- **A slow first reply.** On a cold server the chat says "Waking the demo server. The first reply can take up to a minute." That only applies to a deployed backend, and the app is not deployed yet.
- **The gate comes out green on the weakened prompt.** See step 13: model variance. Re-run with `--repeat 3`, or show the faked-verdict test.
- **Anything unexpected in the browser.** Say what you see. The honest list of what was and was not checked is in the Phase 4 section of `README.md` ("What was verified, and what was not").
