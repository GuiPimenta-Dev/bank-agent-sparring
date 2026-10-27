# Context Map

Sparring is a customer-support agent for Kestrel Bank, continuously stress-tested by Simulated Users whose Failures feed a regression suite.

## Contexts

- [Bank](./src/sparring/bank/CONTEXT.md) — Kestrel Bank's card domain, its Policy, and the backend that enforces Hard Rules
- [Evaluation](./src/sparring/evaluation/CONTEXT.md) — Scenarios, Simulated Users, the Oracle, Failures and the Golden Set
- **Agent** (`src/sparring/agent/`) — the support agent; it has no vocabulary of its own and speaks the Bank's

## Relationships

- **Agent → Bank**: the agent reaches the bank only through its MCP tools; Hard Rules are enforced on the bank's side, and the Verified Session travels outside the model's context (ADR-0010, ADR-0022)
- **Evaluation → Bank**: the Oracle reuses the bank's domain for Hard Rules, and Bank Snapshots are made of the bank's domain objects (ADR-0001, ADR-0003)
- **Evaluation → Agent**: Evaluation drives the agent as the system under test, wired together by composition roots; it never imports the agent (ADR-0007)
