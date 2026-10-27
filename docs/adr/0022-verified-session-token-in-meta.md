# The Verified Session travels as a bank-signed token in `_meta`, never through the model

Supersedes ADR-0010's binding of the Verified Session to the MCP session: MCP protocol revision 2026-07-28 (the SDK's current era) removed sessions — every request is self-contained, with no handshake and no `Mcp-Session-Id`. `verify_identity` now returns a token, HMAC-signed by the bank over the Customer id and expiry, in the tool result's `_meta`; the agent's process (code, not the LLM) keeps it and attaches it to the `_meta` of every later tool call. The token is in no tool's input schema and never enters the model's context. Everything else in ADR-0010 stands: business-level tools, two transports, and no tool takes a `customer_id`, so confused-deputy attacks remain impossible by construction. The server becomes stateless, scales horizontally, and survives restarts; signing uses only the standard library.

## Considered Options

- **Pinning the legacy (session-based) protocol**: keeps ADR-0010 unchanged, but builds on a superseded protocol era and needs sticky sessions to scale.
