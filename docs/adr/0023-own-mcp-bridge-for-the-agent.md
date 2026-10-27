# The agent bridges MCP tools to LangChain itself instead of using langchain-mcp-adapters

`langchain-mcp-adapters` (0.3.1, the latest release) cannot be imported with the MCP SDK 2.x this project uses: it imports `mcp.server.fastmcp`, which MCP 2 removed. Rather than downgrading the MCP SDK to 1.x, `agent/` turns the bank's MCP tools into LangChain tools with a small bridge of its own. The bridge also owns the one behaviour the adapter would have needed customising anyway: keeping each conversation's Verified Session token in the agent's process and attaching it to the `_meta` of every call (ADR-0022). Revisit once the adapter supports MCP 2.

## Considered Options

- **Pinning `mcp<2`**: makes the adapter work, but builds the bank on a superseded SDK and protocol era.
