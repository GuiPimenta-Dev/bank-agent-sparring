# sparring

A customer-support agent for Kestrel Bank, a fictional bank, stress-tested by Simulated Users whose Failures automatically grow a Golden Set in Langfuse.

## Before writing code

- **Vocabulary:** `CONTEXT-MAP.md` lists the contexts and links each one's glossary (`CONTEXT.md`). Use their terms in code and docs, and never the words listed under _Avoid_.
- **Decisions:** `docs/adr/` records why things are the way they are. Don't contradict an ADR silently; propose a new one that supersedes it.
- **Style:** `docs/code-style.md` is binding.

## Commands

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run lint-imports
```

The repository contract suite's Postgres leg runs only with a database; CI always runs it. Locally:

```bash
docker compose -f infra/postgres/docker-compose.yml up -d --wait
SPARRING_TEST_DATABASE_URL=postgresql+asyncpg://sparring:sparring@localhost:55432/sparring uv run pytest
```

Local Langfuse (first time: `cp infra/langfuse/.env.example infra/langfuse/.env`), UI at http://localhost:3000:

```bash
(cd infra/langfuse && docker compose up -d --wait)
```

Talk to the agent as the demo Customer (first time: `cp .env.example .env` and set `ANTHROPIC_API_KEY`). It calls a paid API, so only the user runs it:

```bash
uv run --env-file .env sparring-chat
```

Run one Scenario (or Canonical Case) end-to-end with the real agent and Simulated User — also a paid run, so only with the user's approval:

```bash
uv run --env-file .env sparring-run-scenario canonical_cases/freeze-active-debit-card.json
```

Explore: generate Scenarios from sampled Dimensions and run them under a hard dollar cap; Failures are Reproduced and promoted into the Golden Set. It shows an estimate and asks before starting. A paid run, so only with the user's approval of its cap:

```bash
uv run --env-file .env sparring-explore --scenarios 20 --max-cost 1.00
```

Failure Signatures by frequency over the last days, with each one's share of the Golden Set. It reads Langfuse only and calls no LLM:

```bash
uv run --env-file .env sparring-failures --days 7
```

Regression Check: the Golden Set against this commit's agent, compared with main's baseline, published as the `regression-check` commit status. A paid run, so only with the user's approval of its cap. See `docs/regression-check.md`:

```bash
uv run --env-file .env sparring-regression-check --max-cost 2.00
```

Model sweep: the Golden Set over agent models × effort levels, as Langfuse experiment runs, reporting resolved rate and cost per resolved conversation per cell. The larger models are expensive, so it is a paid run only with the user's approval of its cap:

```bash
uv run --env-file .env sparring-sweep --max-cost 3.00 --models claude-sonnet-5-5,claude-haiku-5-5
```

## Delivering a slice

Each GitHub issue is a vertical slice. A slice is delivered when all of this has happened, in order — it is the end of the work, not an optional step, and needs no separate approval:

1. Every command above passes locally.
2. The work is committed on a branch named `<issue>-<slug>` (e.g. `1-walking-skeleton`).
3. The branch is pushed and a PR is opened whose body starts with `Closes #<issue>` and carries the delivery report: what each acceptance criterion gained, decisions worth recording (and any ADR added or superseded), what was deliberately left out, and the known limits of deliberate simplifications.
4. CI is green on the PR.
5. The PR is merged with a merge commit (`gh pr merge --merge`). Every slice keeps its own PR as history, and the next slice starts from the updated `main`; PRs are never stacked.

Then give the user the PR link.

## Rules for agents

- All code, docs, commit messages and ADRs are in English.
- Exploration, Regression Checks and model sweeps call paid LLM APIs. Never run them without the user's explicit approval for that run and its dollar cap.
- Never write to the Golden Set by hand; it grows only through Promotion.
- The project's total LLM spend is capped at $15 of prepaid provider credit (ADR-0024). Size every run to fit; never suggest raising the cap to get a run through.
- Never write `ponytail` comments in this repository; CI rejects them.
