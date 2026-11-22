# Case study: Eval Discovery

A prototype of one requirement from a product strategy document, built as a working service. This page covers what I chose and why, how it works, and what I did and did not verify. See also the [demo script](DEMO.md) and the [architecture document](ARCHITECTURE.md).

## The strategy document and the requirement I chose

I wrote a strategy document about time-to-value for people who build voice agents without code on an AI voice-agent platform. A builder tests an agent by chatting with it in a **preview** (one test conversation), spots a problem, edits the agent's instructions and previews again. The document proposed several solutions. I prototyped **Solution 2**: detect test cases during preview runs.

The requirement, in my words: when a builder keeps re-previewing the same agent behavior, the system notices, names the behavior, saves it as a criterion plus a runnable test, re-runs those tests on every change to the agent, and says when testing is done.

Why this one:

- **It attacks the real gap.** The strategy document itself says a plain "save as test" nudge does not teach a builder why tests matter. Naming the behavior and showing the evidence from the builder's own runs is my answer to that.
- **It carries an engineering claim.** The behavior a builder keeps probing is a learning signal that comes from the builder, not a benchmark. A fix proposed from that signal and checked against tests it was not tuned on is a small, controlled version of an agent improving its own instructions (only the system prompt changes; no model is trained).
- **It can be built and shown end to end**, from a persisted service with background jobs and a human approval step to a path into the customer's own CI.

**On numbers.** The strategy document's figures are illustrative, not measured, and nothing here is compared with them. This project has no real users, so it reports no usage numbers.

## The design

**The loop.** A builder pastes any agent's system prompt (or picks a preset), chats with it as the customer and saves each run. After two new runs the app checks whether the builder keeps probing the same behavior: a cheap keyword filter first, then a model only when the filter fires. On a loop, the builder can discover a **criterion**: a name, a definition, a good and a bad example, and why it was found. A judge model grades the saved runs against it; the first failing run becomes a **fix test** and the next a **held-out test**. A test is the builder's own scripted customer messages, replayed against the agent's current prompt and graded pass, fail or not applicable. A suggester model proposes a replacement prompt, and the builder reads a diff and a plain-language reason.

**The human gate.** A suggested fix is only a proposal, and chat keeps using the current prompt. Approving it does not make it live either. It starts a **verification**, and the proposal goes live only if the verification passes. The builder can reject a proposal, and can restore any earlier version, which creates a new version instead of rewriting history.

**Held-out verification.** The suggester sees the failing test but never the held-out test, so the fix cannot be tuned to it. If only one failing run exists, the app can generate a second scenario and keeps it only if the current prompt fails it. The server then replays the candidate against the fix test, the held-out test and every test that passed before, which is a regression check. With no held-out test the version is labelled weaker verification, and the screen says so.

**The promotion rule is code, never a model.** A candidate is promoted if and only if the fix test passes, the held-out test passes when there is one, and no previously passing test fails. A model writes an explanation afterward, but it cannot change a verdict or the status: the before-and-after table comes from stored results, and any test id the explanation cites that is not in that table is dropped. The judge never sees the agent's prompt, only the criterion and the transcript, both wrapped as data.

## Engineering depth

- **A database-backed job queue.** Building tests, replaying them and suggesting fixes are model-heavy, so they run as background jobs, not inside a web request. A plain table is the queue: a worker claims the oldest job with `SELECT ... FOR UPDATE SKIP LOCKED`, a job that has run longer than 180 seconds is treated as abandoned and requeued, and each job gets at most 3 attempts. The worker also runs a reconcile step after every pass, so a verification still finishes (promoted or failed) if the process restarts or a job is lost.
- **Postgres and migrations.** The app runs on SQLite locally and is built for Postgres, with three Alembic migrations. Migrations `0002` and `0003` were applied to a throwaway real Postgres on top of populated rows, with downgrade and upgrade round trips.
- **Races, found and fixed.** Two races turned up during development and review: two concurrent prompt edits could take the same version number, and a fix written against an older prompt could end up applied over a newer one. The agent row is now locked before a version is numbered, and a fix remembers the version it was written against, so approval and promotion refuse it if the prompt has moved on. A race test then ran against a real Postgres with a live backend: 15 rounds each of two simultaneous approvals, approve against a manual edit, approve against reject, and approve against restore. Exactly one request won every round and the invariants held (at most one version verifying, exactly one promoted).
- **Prompt-injection hygiene.** Prompts, transcripts and pattern summaries come from users, so every model call wraps them in delimiter tags and tells the model the tagged text is data; tags inside pasted text are neutralized. The "why this change" quote is cut from the stored transcript by code, and model-written text is shown as plain text, so a model cannot fabricate evidence or markup.
- **The CI gate, and its exit-code contract.** `evalgate` takes an exported suite and a prompt file, replays the tests the same way, and fails when the pass rate drops below a threshold. It imports only the pure logic and the model client, never the web framework or the database. Its contract: exit `0` at or above the threshold, `1` below it, `2` for bad usage or an invalid file, and `3` when it could not run (no API key, a test errored, nothing runnable). **It never reports success when it could not run**, so a missing key or a provider outage fails a check like a regression would. By default it runs only the tests that passed at export, so it guards what works and does not fail on a bug you never fixed. The key is read only from the environment and never printed. On GitHub, pull requests from forks get no secrets, so the gate exits `3` there, which is the safe outcome.
- **Fail-fast verification.** Verification used to wait for every replay. A candidate now fails as soon as the stored verdicts decide it: a failing fix or held-out test, or any regression, is decisive even while other replays are still queued. Its still-queued replays are cancelled, so no model calls are spent on a dead candidate. Only failure can be decided early, never promotion.

## How it was built

I wrote the requirement and the design, then built the code with an AI coding assistant working from a written spec and a task plan, one task at a time, tests first, with a separate review of each task before the next. It came in four phases: persistence and any-agent support, tests and replay, the fix loop, and shipping (export, CI gate, simulation, docs). The specs are in [the design spec](superpowers/specs/2026-11-21-agent-ready-eval-flywheel-design.md) and [the Phase 4 addendum](superpowers/specs/2026-11-22-phase4-ship-design.md). The aim was that each claim in these documents traces to code or to a command that was run.

## Simulation results

> **PLACEHOLDER — to be replaced from docs/simulation-results.md after the real run. No numbers have been generated yet.**

The simulation measures one thing: how many preview conversations a simulated builder needs before an independent reference judge passes the agent on a fixed goal (for example "never promises a refund"), with the flywheel and with a manual baseline where the builder rewrites the prompt itself after each failure. A model plays the builder, the sample will be small, and both arms use the same judge model, so the results will be a harness check and a rough comparison, not evidence about real builders. The harness has run in mock mode only, whose numbers are meaningless by design.

## What was and was not verified

**Verified (actually run in the development environment):**

- The backend test suite passes: 1387 tests, with 19 skipped (the real-Postgres tests, which need a database URL). The frontend suite passes 434 tests in 25 files, lint reports no problems and the build succeeds. Every model call in these tests is faked.
- The 19 real-Postgres tests (migrations over populated rows, fail-fast verification, approval races) were run once locally against a throwaway Docker Postgres and passed.
- The mock-mode simulation ran end to end, both arms and both scenarios, and its report rendered.
- The Action and workflow files are parsed in unit tests for structure and security properties, and the example gate suite passes with its real prompt and fails with the weakened one when the model's verdicts are faked.
- In Phase 3 the UI was exercised end to end in a real browser against real Groq, and one real run completed the whole loop. One run is not evidence of general quality.

**Not yet verified:**

- A real-model run of the gate on the example suite.
- A real-model simulation run, so there are no simulation results.
- The GitHub Action and the CI and demo workflows on GitHub, including the Postgres service container and fork pull requests. The CI tests have only ever run locally.
- The layout of the **Export suite for CI** button in a browser (it is unit tested; nobody has looked at it rendered).
- The metrics script on real usage data, a real deploy on Render, and more than one worker process (unsupported).

## Limitations and what I would do next

- **The judge is a model.** A pass is a signal, not proof, and a verdict can flip between runs even at temperature 0. The check that the judge can tell a criterion's good example from its bad one is weak by construction, because it grades text it was just given. The gate's only mitigation is a majority vote across repeated runs.
- **A generated second test can be a light paraphrase of the first.** The copy check only rejects an identical message list.
- **Replays use fixed customer messages,** which can read oddly if the agent's new replies diverge.
- **The simulation is a model playing a builder,** at a tiny sample size, and it favours the baseline in one respect (the baseline's rewriter is shown the full goal, the flywheel's app-driven fix never is). It will say so on its results page.
- **It is a prototype.** There are no accounts, one backend process, no deployment yet, and a deferred list in the README (for example sliding token expiry and deleting a single agent).

What I would do next, in order: run the gate and the simulation for real and publish the results as they come out, whatever they show; run the Action on GitHub once the repository secret exists; deploy and check the real rate-limit behavior behind a proxy; then test the loop on a few real agents with real builders, since this is the part no simulation can stand in for.
