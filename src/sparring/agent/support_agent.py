from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.memory import InMemorySaver
from mcp import Client

from sparring.agent.bank_tools import BankTools

POLICY = (Path(__file__).parent / "policy.md").read_text()


@dataclass(frozen=True)
class AgentTurn:
    reply: str


class SupportAgent:
    """Kestrel Bank's card support agent (ADR-0011).

    One tool-calling loop over the bank's MCP tools with the Policy as its system
    prompt. Conversations are kept by id; each `reply` is one turn of one conversation.
    """

    def __init__(self, graph: Any, callbacks: list[BaseCallbackHandler]) -> None:
        self._graph = graph
        self._callbacks = callbacks

    @classmethod
    async def connect(
        cls, bank: Client, model: str, effort: str | None = None, tracing: bool = False
    ) -> "SupportAgent":
        """An agent on a Claude model, reaching the bank through an MCP client."""
        chat_model = ChatAnthropic(
            model_name=model,
            max_tokens_to_sample=16_000,
            output_config={"effort": effort} if effort else None,
            timeout=None,
            stop=None,
        )
        return await cls.with_model(bank, chat_model, tracing=tracing)

    @classmethod
    async def with_model(
        cls, bank: Client, model: BaseChatModel, tracing: bool = False
    ) -> "SupportAgent":
        """An agent on any chat model, such as a scripted one in tests."""
        tools = await BankTools(bank).load()
        graph = create_agent(  # pyright: ignore[reportUnknownVariableType]
            model, tools, system_prompt=POLICY, checkpointer=InMemorySaver()
        )
        return cls(graph, [CallbackHandler()] if tracing else [])

    async def reply(self, conversation_id: str, message: str) -> AgentTurn:
        config: RunnableConfig = {
            "configurable": {"thread_id": conversation_id},
            "callbacks": self._callbacks,
            "metadata": {"langfuse_session_id": conversation_id},
        }
        state = await self._graph.ainvoke({"messages": [HumanMessage(message)]}, config=config)
        return AgentTurn(state["messages"][-1].text)
