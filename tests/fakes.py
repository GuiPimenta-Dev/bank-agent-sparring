"""Scripted stand-ins for the LLMs, so no test ever calls a real model."""

import itertools
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolCall
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from sparring.evaluation.simulated_user import Turn, UserMove


class ScriptedModel(GenericFakeChatModel):
    """Replays scripted agent messages; LangChain requires subclassing to accept tools."""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


def scripted(*messages: AIMessage) -> ScriptedModel:
    return ScriptedModel(messages=iter(messages))


def unreachable(error: Exception) -> ScriptedModel:
    """A model whose provider keeps failing: every call raises `error`."""

    def failing() -> Iterator[AIMessage]:
        raise error
        yield

    return ScriptedModel(messages=failing())


_call_ids = itertools.count(1)


def calls(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[tool_call(name, **args)])


def calls_in_parallel(*tool_calls: ToolCall) -> AIMessage:
    return AIMessage(content="", tool_calls=list(tool_calls))


def tool_call(name: str, **args: Any) -> ToolCall:
    return {"name": name, "args": args, "id": f"call-{next(_call_ids)}", "type": "tool_call"}


def says(text: str) -> AIMessage:
    return AIMessage(content=text)


class ScriptedSimulatedUser:
    """A SimulatedUserModel that replays scripted moves and keeps what it was shown.

    With `answer_requests` set, it also answers every request its app shows: True for a
    careless Customer who confirms anything, False for one who rejects everything.
    """

    def __init__(self, moves: Iterable[UserMove], answer_requests: bool | None = None) -> None:
        self._moves = iter(moves)
        self._answer_requests = answer_requests
        self.instructions = ""
        self.transcripts: list[list[Turn]] = []

    async def next_move(self, instructions: str, transcript: Sequence[Turn]) -> UserMove:
        self.instructions = instructions
        self.transcripts.append(list(transcript))
        move = next(self._moves)
        if self._answer_requests is None:
            return move
        shown = [
            turn.text.removeprefix("Request ").split(":")[0]
            for turn in transcript
            if turn.speaker == "customer_channel" and turn.text.startswith("Request ")
        ]
        answered = {
            request_id for request_id in shown if f"request {request_id}." in str(transcript)
        }
        decisions = {r: self._answer_requests for r in shown if r not in answered}
        return UserMove(move.message, move.ends_conversation, {**decisions, **move.decisions})
