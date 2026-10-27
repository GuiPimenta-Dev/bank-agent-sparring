# Total project spend is capped at $15 by the providers, not by our code

This is a hobby project, so its whole lifetime of LLM spend is capped at $15: $10 of Anthropic credit (the agent) and $5 of OpenAI credit (the Simulated User and judges), each prepaid with auto-reload turned off. When a balance runs out, the provider refuses further calls, so no bug, forgotten flag or runaway loop in this codebase can exceed the cap. The per-run caps of ADR-0020 remain and keep a single run from consuming the budget at once. The cap is raised only by an explicit decision of the user, never to get a run through.

## Consequences

Any run's cap must fit what remains of the budget. A model sweep on the most expensive models can take a large share of it, so sweeps stay rare and small.
