# The agent runs on Claude; the Simulated User and judges run on a pinned OpenAI model

Applying ADR-0006 with the providers available: the agent runs on Claude through LangChain inside `agent/`, and its model × effort grid (Opus 5.5, Sonnet 5.5, Haiku 5.5) is an experiment variable compared by cost per resolved conversation on the Golden Set. The Simulated User and the LLM judges run on one pinned OpenAI model called through the OpenAI SDK directly, only inside `evaluation/`. A different provider keeps every current Claude model available to the agent sweep and reduces shared blind spots and self-preference between simulator, judge and agent.

## Considered Options

- **Claude Sonnet 5.5 pinned as simulator**: one provider, but removes the most interesting point from the agent sweep.
- **Previous-generation Claude (`claude-sonnet-5`) pinned as simulator**: keeps the full sweep with one provider; it is the fallback if OpenAI access is not available, at the cost of a forced rebaseline when that model is retired.

## Consequences

Because this is a low-cost hobby project (ADR-0020), the agent's default model for Exploration and the Regression Check is `claude-haiku-5-5` (about one cent per conversation). Opus 5.5 and Sonnet 5.5 appear only in occasional, capped model sweeps. A weaker default agent also fails more, which is what the Failure → Golden Set loop needs to show its value. Default per-run caps: Exploration $1, Regression Check $2, model sweep $10.
