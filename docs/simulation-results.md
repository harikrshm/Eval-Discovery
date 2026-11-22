# Simulation results

Simulated results. N requested = 3 per scenario and arm; the N column is the number of trials on file. Models: chat: openai/gpt-oss-20b, judge: openai/gpt-oss-120b, pattern: openai/gpt-oss-120b, criteria: qwen/qwen3.8-27b. Date: 2026-11-22. Commit: eef7fb6.

These are simulated figures and are not measurements of real builders; they are not compared with the strategy document's illustrative targets.

## Results

Preview runs until the first judged pass, simulated, lower is better. Trials that never passed are counted at the cap of 12.

| Scenario | Arm | N | Converged | Median previews (simulated) | Mean previews | Min | Max | Model calls | Tokens | Wall time |
|---|---|---|---|---|---|---|---|---|---|---|
| support-refund | baseline | 3 | 3 | 2.0 | 2.00 | 2 | 2 | 33 | 30157 | 174.2 s |
| support-refund | flywheel | 3 | 3 | 3.0 | 3.00 | 3 | 3 | 87 | 71702 | 453.0 s |
| booking-confirm | baseline | 3 | 2 | 2.0 | 5.00 | 1 | 12 | 84 | 101151 | 642.3 s |
| booking-confirm | flywheel | 3 | 3 | 2.0 | 1.67 | 1 | 2 | 28 | 23678 | 169.5 s |

## Path counts

Shown for the flywheel arm only, among its converged trials. The baseline's only mechanism is manual rewrites.

### support-refund (simulated)

- baseline: n/a (manual rewrites)
- flywheel, loop: 0
- flywheel, fallback: 3
- flywheel, converged with no fix needed: 0 (the first preview(s) passed with no prompt change and no app fix)
- flywheel, did not converge: 0 (listed below, not counted under any path)

### booking-confirm (simulated)

- baseline: n/a (manual rewrites)
- flywheel, loop: 0
- flywheel, fallback: 0
- flywheel, converged with no fix needed: 3 (the first preview(s) passed with no prompt change and no app fix)
- flywheel, did not converge: 0 (listed below, not counted under any path)

## Trials that did not converge

- booking-confirm, baseline, trial 2: no judged pass in 12 previews. Notes: Preview 3 failed: the agent reply could not be generated. Preview 6 failed: the agent reply could not be generated. Prompt rewrite after preview 9 failed: the prompt rewrite was too long. Prompt rewrite after preview 10 failed: the prompt rewrite was too long. Preview 11 failed: the agent reply could not be generated. Prompt rewrite after preview 11 failed: the prompt rewrite was too long.

## Limitations

- Tiny N: a handful of trials per scenario and arm, so medians move a lot between runs and no difference here is statistically established.
- An LLM-played builder, not a human: the customer messages and the baseline's prompt rewrites come from a model, so they are more patient and more literal than a real builder.
- The same model family plays the builder and powers the agent, which can make the conversations easier or more predictable than real ones.
- The reference judge shares the model family with the app's own judge (both judge calls use the same judge model), so the two are not independent in kind; in mock mode independence is structural only.
- The baseline rewriter and the customer-message builder (in both arms) are shown the full goal criterion, as are the flywheel's fallback rewrites; only the app-driven fix steps never see it. This favours the baseline.
- Free-tier pacing: calls are paced to the tokens-per-minute limit, so wall times describe the pacing as much as the work.
- The flywheel arm gets one attempt at the app flow and then falls back to manual rewrites, so a fallback trial starts roughly two previews behind the baseline.
- The app limits each workspace to 30 jobs per hour; every trial uses a fresh workspace so the limit does not bind inside a trial.

These are simulated figures and are not measurements of real builders; they are not compared with the strategy document's illustrative targets.
