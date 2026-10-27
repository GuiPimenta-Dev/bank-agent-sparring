# Langfuse is self-hosted; the Regression Check runs locally and gates merges via a commit status

Langfuse runs self-hosted with Docker Compose, which removes Cloud unit and retention limits (a Golden Set item's `source_trace_id` keeps resolving forever). GitHub Actions cannot reach a local Langfuse, so the expensive Regression Check runs from a local CLI that executes the Golden Set and publishes a `regression-check` commit status (pass / fail / invalid) on the PR via `gh`; branch protection requires that status to merge. GitHub-hosted CI runs everything cheap and LLM-free: lint, pyright, import-linter, tests and the Canonical Case check. Exploration also runs locally, scheduled or on demand.

## Considered Options

- **Self-hosted GitHub Actions runner on the developer machine**: automatic, but on a public repository a fork PR can rewrite the workflow and execute arbitrary code on that machine.
- **Tunnelling the local Langfuse to CI**: exposes every conversation trace to the internet.
- **Langfuse Cloud**: reachable from CI, but the Hobby plan's 50k units and 30-day data access constrain Exploration volume.

## Consequences

The repository is public. If Langfuse later moves to a VM, the same CLI becomes a CI job without code changes.
