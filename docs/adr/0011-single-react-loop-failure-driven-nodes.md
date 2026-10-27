# The agent starts as a single ReAct loop; graph nodes are added only on Failure evidence

The agent is LangGraph's prebuilt tool-calling loop with the whole Policy in a cached system prompt (no retrieval: a few pages of text gain nothing from RAG but a new failure mode). A fixed-flow graph would encode the Policy a third time and break on multi-request conversations; a multi-agent design adds handoff failures for only five operations. Any extra node must be justified by a Failure Signature that prompt changes could not fix, and its effect proven by a Regression Check. The per-turn tool-call cap is both an Invariant and a runtime guard via the graph's recursion limit.

Superseded in part by [ADR-0026](0026-tool-call-cap-enforced-after-each-model-call.md): the runtime guard is a hook after each model call, not the recursion limit.
