---
status: superseded by ADR-0022
---

# The bank is an MCP server whose tools act on the session's verified Customer

The bank exposes business-level tools (one per domain operation, e.g. `report_lost_or_stolen(card_id, reason)`, never CRUD) through one MCP server with two transports: Streamable HTTP for the deployed service and an in-memory transport per simulated conversation, so the real protocol sits in the path of every Exploration run. `verify_identity` binds the Verified Session to the MCP session, and no other tool takes a `customer_id`; this rules out confused-deputy attacks by construction (an injected "look up customer 4471" has no parameter to land in).

## Considered Options

- **Session token returned by `verify_identity` and passed on every call**: works statelessly, but puts the token in the LLM's context where it can be miscopied, mixed up or leaked.

## Consequences

The MCP server must keep per-session state and cannot run in stateless mode.
