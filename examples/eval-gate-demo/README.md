# Eval gate demo

A small, runnable example of the Eval Discovery CI gate. It guards a customer-support agent called Sam against one regression: promising customers refunds.

## What the gate does

`evalgate` replays an exported test suite against an agent prompt, has the judge grade every transcript, and fails the build when the pass rate falls below a threshold. A prompt edit that brings back a bug you already fixed turns the pull request check red.

```
python -m evalgate --suite examples/eval-gate-demo/suite.json --prompt examples/eval-gate-demo/prompt.md
```

By default the gate runs only the tests that were passing when the suite was exported, because those are the behaviours you want to protect. Tests that were failing or never graded are skipped, and the skip count is printed. Pass `--all` to run them too. In this example, `test-regression-1` was exported as `fail`, so the default run skips it.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Every graded test passed at or above the threshold. |
| 1 | The pass rate is below the threshold. |
| 2 | Bad usage: invalid arguments, or a missing or invalid suite or prompt file. |
| 3 | The gate could not run: `GROQ_API_KEY` is not set, a test errored, no tests were runnable, or none was applicable. |

The gate never reports success when it could not run. Exit code 3 fails the check just like exit code 1, so a missing key or a provider outage cannot let a bad prompt through.

## Files

- `prompt.md`: Sam's prompt, with an explicit refund guardrail ("never promise or guarantee a refund; ask for the order number and escalate").
- `weakened-prompt.md`: the same prompt without the guardrail.
- `suite.json`: an `eval-discovery.suite` v1 export with one criterion, "Does not promise refunds". It holds a fix test and a held-out test, both passing at export, with customers who push for a refund guarantee. It also holds one regression test that was failing at export.

## Export a suite from the app

1. Open your agent in Eval Discovery and let the run, fix and held-out check finish.
2. In the evaluation criteria panel, click **Export suite for CI**.
3. Save the downloaded JSON file in your repository, for example `evals/suite.json`.
4. Keep the agent prompt in a file in the repository too, for example `prompts/agent.md`. The gate tests that file, not the prompt text stored inside the export.

The export contains the criteria and the scripted customer messages. It does not contain any API key.

## Wire the Action into your repository

1. Reference the Action from the repository that hosts it (this one). This is the supported way: the Action runs `python -m evalgate` with `PYTHONPATH` set to the root of the Action's own repository, so it needs the `evalgate/` and `backend/` packages and `requirements-gate.txt` that live there. A bare copy of `.github/actions/eval-gate` fails with "No module named evalgate". If you must copy it, copy `evalgate/`, `backend/` and `requirements-gate.txt` too, keeping the same layout relative to `.github/actions/eval-gate` (that is, at the repository root).
2. Add a repository secret named `GROQ_API_KEY` (Settings, Secrets and variables, Actions).
3. Add a workflow, replacing `OWNER/REPO` with the repository that hosts the Action and `REF` with a tag, branch or commit SHA of it (pin a commit SHA if you want it immutable):

```yaml
name: Eval gate
on:
  pull_request:
    paths:
      - prompts/agent.md
permissions:
  contents: read
jobs:
  eval-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: OWNER/REPO/.github/actions/eval-gate@REF
        with:
          suite: evals/suite.json
          prompt: prompts/agent.md
          groq-api-key: ${{ secrets.GROQ_API_KEY }}
```

Optional inputs: `threshold` (default `1.0`), `repeat` (default `1`; a test passes on a strict majority of its runs, which smooths out model noise), `python-version` (default `3.13`) and `extra-args` (extra `evalgate` flags such as `--all --pause 2`). The result table is printed to the job log and written to the job summary.

This repository's own workflow, `.github/workflows/eval-gate-demo.yml`, does the same for the files in this folder, using the local path `./.github/actions/eval-gate` because the Action lives in the same repository. Remember that the Action has not been run on GitHub yet (see below), so this wiring is untested there.

### Pull requests from forks

GitHub does not give secrets to workflows triggered by pull requests from forks. On a fork pull request `GROQ_API_KEY` is empty, so the gate exits with code 3 ("could not run") and the check fails. That is the safe behaviour. Do not switch the trigger to `pull_request_target` to work around it: that would run untrusted prompt text with your key. Run the gate on branches in the main repository, or re-run it yourself after reviewing a fork's change.

## Not yet run on GitHub

The Action and the demo workflow have NOT been run on GitHub from the development environment of this repository. What has been tested is the `evalgate` command line (with the model calls faked), the structure and security properties of the YAML files (parsed in unit tests), and this example's pass and fail behaviour with faked model calls. A first real run on GitHub, with a real `GROQ_API_KEY`, is still to be done and may reveal problems that those tests cannot.

## Try the demo

1. Create a branch and a pull request that changes `examples/eval-gate-demo/prompt.md`, for example by copying the weakened prompt over it:

   ```
   cp examples/eval-gate-demo/weakened-prompt.md examples/eval-gate-demo/prompt.md
   ```

2. The workflow runs on the pull request. Without the guardrail Sam tends to give in when a customer demands a refund guarantee, the judge fails the criterion, the pass rate drops below 1.0 and the check turns red.
3. Restore the original `prompt.md` and push again: the check turns green.

Model output varies between runs, so a single run can occasionally disagree with the expected result. Setting `repeat: "3"` makes the verdict more stable.
