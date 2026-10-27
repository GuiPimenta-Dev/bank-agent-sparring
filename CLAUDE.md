# sparring

A customer-support agent for Kestrel Bank, a fictional bank, stress-tested by Simulated Users whose Failures automatically grow a Golden Set in Langfuse.

## Before writing code

- **Vocabulary:** `CONTEXT.md` is the glossary. Use its terms in code and docs, and never the words listed under _Avoid_.
- **Decisions:** `docs/adr/` records why things are the way they are. Don't contradict an ADR silently; propose a new one that supersedes it.
- **Style:** `docs/code-style.md` is binding.

## Commands

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run lint-imports
```

## Delivering a slice

Each GitHub issue is a vertical slice. A slice is delivered when all of this has happened, in order — it is the end of the work, not an optional step, and needs no separate approval:

1. Every command above passes locally.
2. The work is committed on a branch named `<issue>-<slug>` (e.g. `1-walking-skeleton`).
3. The branch is pushed and a PR is opened whose body starts with `Closes #<issue>` and carries the delivery report: what each acceptance criterion gained, decisions worth recording (and any ADR added or superseded), what was deliberately left out, and the known limits of deliberate simplifications.
4. CI is green on the PR.

Then give the user the PR link. Never merge a PR: the user reviews and merges every one, to keep control of what lands on `main`.

## Rules for agents

- All code, docs, commit messages and ADRs are in English.
- Exploration, Regression Checks and model sweeps call paid LLM APIs. Never run them without the user's explicit approval for that run and its dollar cap.
- Never write to the Golden Set by hand; it grows only through Promotion.
- Never write `ponytail` comments in this repository; CI rejects them.
