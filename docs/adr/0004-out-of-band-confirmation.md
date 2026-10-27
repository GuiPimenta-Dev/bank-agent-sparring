# Irreversible actions are confirmed out-of-band, never by the agent

Irreversible operations (Lost or Stolen Report, Dispute with Provisional Credit) become Pending Actions that the backend executes only after a Confirmation arriving through the Customer's own channel, with a summary rendered by the backend from the structured action. The agent has no tool to confirm on the Customer's behalf. Text-based confirmation would need an LLM to decide whether the Customer said "yes", which ADR-0002 forbids as a basis for a Failure; out-of-band Confirmation makes it a Hard Rule and keeps the agent from misrepresenting what is being confirmed.

## Consequences

The Simulated User gets its own Confirmation action. A wrong Pending Action that a careless persona confirms surfaces as a final-state mismatch; one that a careful persona rejects forces the conversation to recover.

## Considered Options

- **MCP elicitation**: the server asks the MCP client (the agent's process) to collect the Confirmation without involving the LLM. Rejected because the Confirmation would still travel through the agent's infrastructure, which could forge it if compromised; Confirmations go through a separate Customer Channel instead. Elicitation remains fine for non-sensitive input such as "which of your two cards?".
