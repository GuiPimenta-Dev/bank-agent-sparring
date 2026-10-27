"""Scripted stand-ins for the LLMs, so no test ever calls a real model."""

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
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


def calls(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name}])


def says(text: str) -> AIMessage:
    return AIMessage(content=text)


class ScriptedSimulatedUser:
    """A SimulatedUserModel that replays scripted moves and keeps what it was shown."""

    def __init__(self, moves: Iterable[UserMove]) -> None:
        self._moves = iter(moves)
        self.instructions = ""
        self.transcripts: list[list[Turn]] = []

    async def next_move(self, instructions: str, transcript: Sequence[Turn]) -> UserMove:
        self.instructions = instructions
        self.transcripts.append(list(transcript))
        return next(self._moves)
