from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]
from langchain.agents.middleware import AgentState, after_model
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.runtime import Runtime
from mcp import Client

from sparring.agent.bank_tools import BankTools

POLICY = (Path(__file__).parent / "policy.md").read_text()

TOOL_CALL_CAP = 8
"""The most tool calls one turn may request (ADR-0026). Evaluation checks the same cap
independently, as an Invariant."""

CAP_REACHED_REPLY = "Sorry, I couldn't finish that. Could you tell me once more what you need?"


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    refused_by: str | None
    """The Hard Rule that refused the call, if one did."""


@dataclass(frozen=True)
class TokenUsage:
    """What the model consumed; `input_tokens` excludes input read from or written to the
    prompt cache."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class AgentTurn:
    reply: str
    tool_calls: list[ToolCall]
    """Every tool call the agent requested in this turn, in order, including the ones the
    tool-call cap kept from running."""
    usage: TokenUsage = TokenUsage()
    """Every model call of the turn, added up."""


class AgentUnavailable(Exception):
    """The model provider still failed after the SDK's own retries (ADR-0017)."""


@dataclass(frozen=True)
class Memory:
    """Where conversations are kept between turns; opaque outside the agent."""

    checkpointer: BaseCheckpointSaver[Any]


@asynccontextmanager
async def persistent_memory(database_url: str) -> AsyncGenerator[Memory]:
    """Conversations kept in Postgres, so they survive a restart of the agent's process."""
    async with AsyncPostgresSaver.from_conn_string(database_url) as checkpointer:
        await checkpointer.setup()
        yield Memory(checkpointer)


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
        cls,
        bank: Client,
        model: str,
        effort: str | None = None,
        tracing: bool = False,
        memory: Memory | None = None,
    ) -> "SupportAgent":
        """An agent on a Claude model, reaching the bank through an MCP client.
        Conversations are kept in memory unless `memory` says otherwise."""
        chat_model = ChatAnthropic(
            model_name=model,
            max_tokens_to_sample=16_000,
            output_config={"effort": effort} if effort else None,
            timeout=None,
            stop=None,
        )
        return await cls.with_model(bank, chat_model, tracing=tracing, memory=memory)

    @classmethod
    async def with_model(
        cls,
        bank: Client,
        model: BaseChatModel,
        tracing: bool = False,
        memory: Memory | None = None,
    ) -> "SupportAgent":
        """An agent on any chat model, such as a scripted one in tests."""
        tools = await BankTools(bank).load()
        graph = create_agent(  # pyright: ignore[reportUnknownVariableType]
            model,
            tools,
            system_prompt=POLICY,
            middleware=[_cap_tool_calls],
            checkpointer=memory.checkpointer if memory else InMemorySaver(),
        )
        return cls(graph, [CallbackHandler()] if tracing else [])

    async def stream(self, conversation_id: str, message: str) -> AsyncIterator[str]:
        """The turn's text as the model writes it, a piece at a time. The whole reply, the
        tool-call cap's fallback included, is the last agent message in `history`."""
        stream = self._graph.astream(
            {"messages": [HumanMessage(message)]},
            config=self._config(conversation_id),
            stream_mode="messages",
        )
        try:
            async for chunk, _ in stream:
                if isinstance(chunk, AIMessageChunk) and chunk.text:
                    yield chunk.text
        except anthropic.APIError as error:
            if _retries_ran_out(error):
                raise AgentUnavailable(str(error)) from error
            raise

    async def history(self, conversation_id: str) -> list[tuple[str, str]]:
        """What the Customer and the agent said so far, as (speaker, text), tool calls left
        out."""
        state = await self._graph.aget_state(self._config(conversation_id))
        messages: list[BaseMessage] = state.values.get("messages", [])
        return [
            ("customer" if isinstance(m, HumanMessage) else "agent", m.text)
            for m in messages
            if isinstance(m, HumanMessage | AIMessage) and m.text
        ]

    def _config(self, conversation_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": conversation_id},
            "callbacks": self._callbacks,
            "metadata": {"langfuse_session_id": conversation_id},
        }

    async def reply(self, conversation_id: str, message: str) -> AgentTurn:
        config = self._config(conversation_id)
        try:
            state = await self._graph.ainvoke({"messages": [HumanMessage(message)]}, config=config)
        except anthropic.APIError as error:
            if _retries_ran_out(error):
                raise AgentUnavailable(str(error)) from error
            raise
        turn = _this_turn(state["messages"])
        results = {m.tool_call_id: m for m in turn if isinstance(m, ToolMessage)}
        tool_calls = [
            ToolCall(call["name"], call["args"], _refusal(results.get(str(call["id"]))))
            for m in turn
            if isinstance(m, AIMessage)
            for call in m.tool_calls
        ]
        return AgentTurn(turn[-1].text, tool_calls, _usage(turn))


@after_model(can_jump_to=["end"])
def _cap_tool_calls(state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
    """Ends the turn with a fallback reply instead of running the calls that would take it
    past the cap, and answers them so the conversation stays valid for the next turn."""
    turn = _this_turn(state["messages"])
    requested = [call for m in turn if isinstance(m, AIMessage) for call in m.tool_calls]
    last = turn[-1]
    if not isinstance(last, AIMessage) or not last.tool_calls or len(requested) <= TOOL_CALL_CAP:
        return None
    not_run = [
        ToolMessage(
            "Not run: this turn reached its tool-call cap.",
            tool_call_id=call["id"],
            name=call["name"],
            status="error",
        )
        for call in last.tool_calls
    ]
    return {"messages": [*not_run, AIMessage(CAP_REACHED_REPLY)], "jump_to": "end"}


def _this_turn(messages: Sequence[BaseMessage]) -> Sequence[BaseMessage]:
    start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    return messages[start:]


def _usage(turn: Sequence[BaseMessage]) -> TokenUsage:
    """LangChain counts cached input inside `input_tokens`; it is taken out again here."""
    reported = [m.usage_metadata for m in turn if isinstance(m, AIMessage) and m.usage_metadata]
    details = [usage.get("input_token_details", {}) for usage in reported]
    cache_read = sum(d.get("cache_read") or 0 for d in details)
    cache_write = sum(
        (d.get("cache_creation") or 0)
        + (d.get("ephemeral_5m_input_tokens") or 0)
        + (d.get("ephemeral_1h_input_tokens") or 0)
        for d in details
    )
    return TokenUsage(
        input_tokens=sum(usage["input_tokens"] for usage in reported) - cache_read - cache_write,
        output_tokens=sum(usage["output_tokens"] for usage in reported),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


def _refusal(result: ToolMessage | None) -> str | None:
    return result.artifact if result and isinstance(result.artifact, str) else None


def _retries_ran_out(error: anthropic.APIError) -> bool:
    """Whether the error is one the SDK retries, so it was raised only once retries ran out."""
    if isinstance(error, anthropic.APIStatusError):
        return error.status_code in (408, 409, 429) or error.status_code >= 500
    return isinstance(error, anthropic.APIConnectionError)
