from typing import Any, Final

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from mcp import Client
from mcp_types import TextContent, Tool

# Part of the bank's tool contract: where the Verified Session token travels (ADR-0022).
SESSION_META_KEY: Final = "bank.kestrel/verified-session"


class BankTools:
    """The bank's MCP tools as LangChain tools.

    Each conversation's Verified Session token is kept here, in the agent's process,
    and attached to the `_meta` of every call; it never enters the model's context.
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self._tokens: dict[str, str] = {}

    async def load(self) -> list[BaseTool]:
        listed = await self._client.list_tools()
        return [self._as_langchain_tool(tool) for tool in listed.tools]

    def _as_langchain_tool(self, tool: Tool) -> BaseTool:
        async def call(config: RunnableConfig, **arguments: Any) -> str:
            conversation = str(config.get("configurable", {})["thread_id"])
            token = self._tokens.get(conversation)
            result = await self._client.call_tool(
                tool.name, arguments, meta={SESSION_META_KEY: token} if token else None
            )
            issued = (result.meta or {}).get(SESSION_META_KEY)
            if isinstance(issued, str):
                self._tokens[conversation] = issued
            return "\n".join(
                block.text for block in result.content if isinstance(block, TextContent)
            )

        return StructuredTool.from_function(
            coroutine=call,
            name=tool.name,
            description=tool.description or "",
            args_schema=tool.input_schema,
        )
