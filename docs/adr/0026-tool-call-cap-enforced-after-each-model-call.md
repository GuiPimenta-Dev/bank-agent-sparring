# The tool-call cap is enforced after each model call, not by the recursion limit

ADR-0011 planned the per-turn tool-call cap's runtime guard as the graph's recursion limit. That limit counts graph steps rather than tool calls, so a model that requests several calls at once passes the cap without reaching it. When it is reached, it also raises an exception mid-turn, which can leave the conversation holding tool calls with no results; Anthropic's API rejects the next turn of such a conversation. The guard is therefore a hook that runs after every model call. It counts the tool calls requested so far in the turn, and once they pass the cap (8), it ends the turn with a fixed fallback reply instead of running the batch that crossed it. It also gives each of those calls a "not run" result, so the conversation stays valid for the Customer's next message.

Evaluation checks the same cap independently, as the `tool_call_cap` Invariant over the requested tool calls, so a turn the guard stopped is always also an Invariant violation.

## Considered Options

- **LangChain's `ToolCallLimitMiddleware`**: does the same counting, but its final message is written for developers ("Tool call limit reached: run limit exceeded (9/8 calls)") and would be shown to the Customer and kept in the conversation.
- **Recursion limit, then repairing the conversation after the exception**: needs to find and answer the unanswered tool calls from outside the graph, and still misses parallel calls.
