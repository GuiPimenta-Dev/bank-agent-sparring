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

## Rules for agents

- All code, docs, commit messages and ADRs are in English.
- Exploration, Regression Checks and model sweeps call paid LLM APIs. Never run them without the user's explicit approval for that run and its dollar cap.
- Never write to the Golden Set by hand; it grows only through Promotion.
