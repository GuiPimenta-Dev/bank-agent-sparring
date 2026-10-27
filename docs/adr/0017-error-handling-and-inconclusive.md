# Errors: a two-level Hard Rule hierarchy, sanitized tool errors, and Inconclusive conversations

Hard Rule refusals are expected business outcomes raised as exceptions in a hierarchy at most two levels deep: one base `HardRuleRefusal` with leaves directly under it (`DisputeWindowExpired`, `TransactionPending`, ...) and no intermediate classes. The MCP adapter catches only the base, in one place, and returns a structured refusal to the agent; the leaf name is the label of the Hard Rule refusal metric. Invalid tool arguments rejected by Pydantic return a structured error telling the model what to fix. Unexpected exceptions return a generic error with no stack trace or internals, since anything the LLM sees can leak to the Customer; details go to the Langfuse trace.

A conversation interrupted by an infrastructure failure after retries is Inconclusive, never a Failure, so a bad night at a provider cannot flood the Golden Set. If the Inconclusive rate of a Regression Check exceeds a threshold, the check is invalid — it neither passes nor blocks — and must be re-run.

## Considered Options

- **One exception carrying a `HardRule` enum**: avoids any hierarchy, but the hierarchy is Python's native mechanism for `except` and reads more naturally here.
- **Result-type return values**: explicit, but verbose and unidiomatic in Python.
