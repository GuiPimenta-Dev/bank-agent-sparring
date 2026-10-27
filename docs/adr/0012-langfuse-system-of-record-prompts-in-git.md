# Langfuse is the system of record for evaluation results; prompts stay in git

`evaluation/` has no database of its own. Every simulated conversation is one Langfuse trace (production uses one trace per agent turn, grouped by session) carrying the Scenario id, its Dimension values, the agent version (git SHA) and the simulator model as metadata, plus scores: deterministic ones (`oracle_pass`, `invariant_violation`, `failure_signature`) that may drive Promotion, and LLM-judge ones (`drift_suspected`, `tone`) that may only flag or block. Adaptive Sampling and Failure Signature counts fetch raw scores for a time window via the Scores API and aggregate in Python, avoiding the Metrics API's limits on grouping by metadata.

The Policy text and system prompt live in git, not in Langfuse Prompt Management: a prompt edited in the Langfuse UI would change the agent's behaviour without passing through a PR and its Regression Check.
