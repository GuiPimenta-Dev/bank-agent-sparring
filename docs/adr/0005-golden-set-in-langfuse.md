# The Golden Set lives in Langfuse Datasets, append-only

Promotion must be fully automatic, so the Golden Set's single source of truth is a Langfuse Dataset rather than files in git: promoting is creating an item (with `source_trace_id` linking back to the failing conversation), and dataset runs give experiment comparison for free. To keep evaluations reproducible without git, the Golden Set is append-only and its items are immutable — a Scenario is never edited or deleted, only archived — so "the Golden Set at time T" is every item created before T and not archived before T.

## Considered Options

- **Scenario files in git, mirrored to Langfuse**: versioned and reviewable, but automatic Promotion would require a bot that either opens PRs (friction, no longer automatic) or commits to main.

## Consequences

Running evaluations requires a reachable Langfuse instance. If that becomes a problem, an export of the Dataset to files is a small script.
