# Regression Check

The Regression Check plays the Golden Set against a proposed version of the agent and compares each Scenario's Status with main's. It runs locally, because Langfuse does, and publishes a `regression-check` commit status that branch protection can require ([ADR-0019](adr/0019-self-hosted-langfuse-local-regression-gate.md)).

It calls paid APIs. Run it only with a dollar cap that fits what is left of the project's budget ([ADR-0024](adr/0024-total-spend-capped-by-providers.md)).

## How it decides

Each Scenario runs once. Only a Failure is run again, up to two more times ([ADR-0020](adr/0020-cost-is-a-first-class-constraint.md)):

| Runs | Scenario Status |
|---|---|
| The first run passes | Passing |
| A rerun passes after a Failure | Flaky |
| Every run fails | Failing |
| No run is conclusive | Inconclusive |
| The budget ran out first | Not run |

Then each Scenario is compared with main's latest baseline for the same simulator model:

- **fail** (blocks the merge): a Scenario Passing on main is Failing now. The status names the Scenarios.
- **pass**: no Regression. A Scenario that went from Passing to Flaky is reported as a warning, and a Scenario promoted since the baseline is reported as new. Neither blocks.
- **invalid**: the result cannot be trusted, so it neither passes nor blocks. This happens when there is no baseline for main with this simulator model, when the budget ran out before every Scenario ran, or when more than 20% of the conversations were Inconclusive. Re-run it.

Every check and every baseline is kept in Langfuse as a `regression_check` score on the `regression-checks` session. Its metadata holds every Scenario's Status.

## Commands

The working tree must be clean, so that the check describes one commit.

Record main's baseline. Do this after merging to main, and always after changing `SPARRING_SIMULATOR_MODEL`, since a new simulator model makes every old baseline incomparable ([ADR-0006](adr/0006-pinned-simulator-model.md)):

```bash
git checkout main && git pull
uv run --env-file .env sparring-regression-check --baseline --max-cost 2.00
```

Check a branch and publish the status on its head commit:

```bash
git checkout my-branch
uv run --env-file .env sparring-regression-check --max-cost 2.00
```

The command shows an estimate and asks before starting; `--yes` skips the question. It exits with 0 on pass, 1 on fail and 2 on invalid.

## Requiring it before merging

GitHub only enforces the gate once branch protection requires the status. A repository admin enables it once:

1. Open the repository's **Settings → Branches** and add or edit the rule for `main`.
2. Turn on **Require status checks to pass before merging**.
3. Search for `regression-check` and select it. It appears in the list once the command has published it at least once.
4. Keep `checks`, the CI job, required as well.

The same rule from the command line:

```bash
gh api --method PUT repos/{owner}/{repo}/branches/main/protection \
  -F required_status_checks[strict]=true \
  -f 'required_status_checks[contexts][]=checks' \
  -f 'required_status_checks[contexts][]=regression-check' \
  -F enforce_admins=false -F required_pull_request_reviews=null -F restrictions=null
```
