# Expected Outcomes are computed by a deterministic Oracle, not by an LLM

Failures are promoted into the Golden Set automatically, so a wrong Expected Outcome would silently poison the regression suite and penalize the agent for correct behavior. We therefore implement the Policy twice: as natural-language text that the agent reads, and as deterministic code (the Oracle) that computes every Scenario's Expected Outcome from its initial backend state and structured Goal. LLMs only generate situations (initial state, persona, narrative Goal); they never decide what the correct outcome is.

## Considered Options

- **LLM-generated Expected Outcomes**: scales, but is circular (an LLM grading an LLM); when they disagree there is no way to know which one is right.
- **Hand-written Expected Outcomes**: trustworthy, but the simulation could never discover new Scenarios on its own.

## Consequences

The Policy text and the Oracle can drift apart. Drift is treated as an explicit Scenario fault, not an agent Failure, and must never be promoted into the Golden Set.
