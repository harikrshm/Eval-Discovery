# Case study: Eval Discovery

A prototype of one requirement from a product strategy document, built as a working service. This page covers what I chose and why, how it works, and what I did and did not verify. See also the [demo script](DEMO.md) and the [architecture document](ARCHITECTURE.md).

## The strategy document and the requirement I chose

I wrote a strategy document about time-to-value for people who build voice agents without code. A builder tests an agent by chatting with it in a **preview** (one test conversation), spots a problem, edits the agent's instructions and previews again. I prototyped **Solution 2** from that document: detect test cases during preview runs.

The requirement, in my words: when a builder keeps re-previewing the same agent behavior, the system notices, names the behavior, saves it as a criterion plus a runnable test, re-runs those tests on every change to the agent, and says when testing is done.

Why this one: the strategy document says a plain "save as test" nudge does not teach a builder why tests matter, so naming the behavior and showing evidence from the builder's own runs is my answer. It also carries an engineering claim: the behavior a builder keeps probing is a learning signal from the builder, not a benchmark, and a fix proposed from it and checked against tests it was not tuned on is a small, controlled version of an agent improving its own instructions (only the system prompt changes; no model is trained).

**On numbers.** The strategy document's figures are illustrative, not measured, and nothing here is compared with them. This project has no real users, so it reports no usage numbers.

## The design

**The loop.** A builder pastes any agent's system prompt, chats with it as the customer and saves each run. After two new runs the app checks whether the builder keeps probing the same behavior (a keyword filter first, then a model only when it fires). On a loop the builder can discover a **criterion** (a name, a definition, a good and a bad example, and why it was found). A judge model grades the saved runs against it; the first failing run becomes a **fix test** and the next a **held-out test**, kept aside so a fix can be checked on a case it was not written for. A test is the builder's own scripted customer messages, replayed against the current prompt. A suggester model proposes a replacement prompt, shown as a diff with a plain-language reason.

**The human gate and the promotion rule.** A suggested fix is only a proposal and chat keeps using the current prompt. Approving it starts a **verification**: the server replays the candidate against the fix test, the held-out test (which the suggester never sees) and every test that passed before. It is promoted if and only if the fix test passes, the held-out test passes when there is one, and no previously passing test fails. That rule is code, never a model; a model only writes an explanation afterward and cannot change a verdict or the status. The judge never sees the agent's prompt. The builder can reject a proposal or restore any earlier version.

## Engineering depth

Details are in the [architecture document](ARCHITECTURE.md).

- **Job queue.** Model-heavy work runs as background jobs in a plain database table, claimed with `SELECT ... FOR UPDATE SKIP LOCKED`, with abandoned-job requeue, at most 3 attempts and a reconcile sweep so a verification still finishes after a restart.
- **Races, found and fixed.** Concurrent edits could take the same version number, and a fix could land over a newer prompt; the agent row is now locked and a fix remembers the version it was written against. A real-Postgres race test (15 rounds per race) had exactly one request win every round.
- **Prompt-injection hygiene.** Text from users is wrapped in delimiter tags and treated as data in every model call, and model-written text is shown as plain text.
- **The CI gate.** `evalgate` replays an exported suite against a prompt file and fails below a threshold. Exit `0` passes, `1` is below the threshold, `2` is bad usage and `3` means it could not run, so **it never reports success when it could not run**. By default it runs only tests that passed at export. Fork pull requests get no secrets, so it exits `3` there, which is the safe outcome.
- **Fail-fast verification.** A candidate fails as soon as stored verdicts decide it, and its queued replays are cancelled. Only failure can be decided early, never promotion.

## How it was built

I wrote the requirement and the design, then built the code with an AI coding assistant working from a written spec and task plan, one task at a time, tests first, with a separate review of each task, in four phases (persistence, tests and replay, the fix loop, shipping). The specs are [the design spec](superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md) and [the Phase 4 addendum](superpowers/specs/2026-11-22-phase4-ship-design.md). Each claim here is meant to trace to code or to a command that was run.

## Simulation results

The simulation measures how many preview conversations a simulated builder needs before a reference judge passes the agent on a fixed goal (for example "never promises a refund"), with the flywheel and with a manual baseline where the builder rewrites the prompt itself after each failure. The reference judge is separate from the app's own judge calls (it grades the scenario's own goal and never sees the agent's prompt) but uses the same judge model. A model plays the builder and N is 3 per scenario and arm, so this is not evidence about real builders. Full table, path counts and limitations: [simulation-results.md](simulation-results.md) (first real run, 2026-11-22, commit `eef7fb6`, 12 trials; raw trial files committed under `simulation/results/real/`). Previews until the first judged pass, lower is better, non-converged trials counted at the cap of 12:

| Scenario | Arm | Converged | Median | Mean | Min | Max |
|---|---|---|---|---|---|---|
| support-refund | baseline | 3 of 3 | 2.0 | 2.00 | 2 | 2 |
| support-refund | flywheel | 3 of 3 | 3.0 | 3.00 | 3 | 3 |
| booking-confirm | baseline | 2 of 3 | 2.0 | 5.00 | 1 | 12 |
| booking-confirm | flywheel | 3 of 3 | 2.0 | 1.67 | 1 | 2 |

**This run did not show the flywheel reducing preview runs.**

- **support-refund.** The flywheel needed one more preview than the baseline in every trial (3 against 2) and 87 model calls against 33. All 3 flywheel trials ended on the fallback path (loop path count 0): in 2 the app's fix test did not fail on the current prompt in 3 replays, and in 1 the app built no fix test for the discovered criterion, so the app-driven fix never produced a promoted fix. The design favours the baseline by construction (a flywheel trial that falls back starts roughly two previews behind it), so 3 against 2 is not pure flywheel inefficiency.
- **booking-confirm.** The flywheel converged in 1, 2 and 2 previews, all with "no fix needed": the weak prompt passed the reference judge unchanged. In flywheel trials 2 and 3 the first preview did not pass and the second passed with no change at all, and baseline trial 3 passed in 1 preview with no change, which fits judge and reply variance and a scenario that does not reliably elicit the failure it was built to. The lower flywheel mean (1.67 against 5.00) is therefore not a flywheel effect. The 5.00 baseline mean comes from one trial that never converged in 12 previews; its notes show failures in the harness's model calls (the agent reply could not be generated on 3 previews, prompt rewrites rejected as too long 3 times; cause not established), not a verdict on manual rewriting.
- **What it does not show.** With N = 3 per arm nothing is statistically established in either direction, and the app-driven fix path (the loop, not the fallback) was never exercised end to end with a real model.

The harness ran end to end against a real model, counted every trial and listed its failures instead of dropping them, although one baseline trial hit failures in the harness's model calls that still need fixing. The first real result is a null result, which is a finding.

The gate was also run once per example prompt with real Groq models; the weakened prompt turned it red, but only one of its two tests failed (details and raw output in the verified list below).

## What was and was not verified

**Verified (actually run in the development environment):**

- The backend suite passes 1404 tests with 19 skipped (the real-Postgres tests); the frontend suite passes 436 tests in 25 files, lint reports no problems and the build succeeds. Every model call in these tests is faked. The 19 Postgres tests (migrations over populated rows, fail-fast verification, approval races) were run twice locally against a throwaway Docker Postgres (before and after the final fix wave) and passed both times.
- The mock-mode simulation ran end to end, both arms and both scenarios, and its report rendered. The real simulation ran too (12 trials, results above), and the gate ran once per example prompt against a real model (raw output in [gate-real-run/](gate-real-run/), one run each, `--repeat` 1). With `prompt.md` it exited 0 at pass rate 1.0 (2 of 2 tests passed; 1 was skipped because its verdict at export was not "pass"). With `weakened-prompt.md` it exited 1 at 0.5: the fix test failed (the judge's rationale quotes the reply as "... we will issue a full refund") and the held-out test passed. So the weakened prompt turned the gate red, but only one of its two tests failed, and with a real model that outcome is probabilistic. Neither run shows how often an outcome recurs.
- The Action and workflow files are parsed in unit tests for structure and security properties, and the example gate suite passes with its real prompt and fails with the weakened one when the model's verdicts are faked.
- In Phase 3 an end-to-end run with real Groq completed the loop (see ARCHITECTURE 6.13); one run is not evidence of general quality.

**Not yet verified:**

- The app-driven fix loop inside the simulation with a real model (every flywheel trial that needed a fix fell back), and the gate on more than one run per prompt.
- The GitHub Action and the CI and demo workflows on GitHub, including the Postgres service container and fork pull requests. The CI tests have only ever run locally.
- The layout of the **Export suite for CI** button in a browser (it is unit tested; nobody has looked at it rendered), and the Phase 4 changes to the verification view.
- The metrics script on real usage data, a real deploy on Render, and more than one worker process (unsupported).

## Limitations and what I would do next

- **The judge is a model.** A pass is a signal, not proof, and a verdict can flip between runs even at temperature 0. Its good-versus-bad-example check is weak by construction, because it grades text it was just given. The gate's only mitigation is a majority vote over repeated runs.
- **A generated second test can be a light paraphrase of the first,** and replays use fixed customer messages, which can read oddly if the agent's new replies diverge.
- **The simulation is a model playing a builder** at a tiny sample size, and it favours the baseline in two respects: a flywheel trial that falls back starts roughly two previews behind it, and the baseline rewriter and the customer-message builder in both arms are shown the full goal criterion, as are the flywheel's fallback rewrites, and only the app-driven fix steps never see it. The results page says so.
- **It is a prototype.** There are no accounts, one backend process, no deployment yet, and a deferred list in the README (for example sliding token expiry and deleting a single agent).

What I would do next, in order: make the simulation able to measure something (scenarios that fail reliably on the weak prompt, a robust fix-test confirmation step, fixes for the harness failures, a larger N) and publish whatever it then shows; run the Action on GitHub once the repository secret exists; deploy and check the real rate-limit behavior behind a proxy; then test the loop on a few real agents with real builders, since this is the part no simulation can stand in for.
