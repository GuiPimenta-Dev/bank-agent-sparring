from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from openai import AsyncOpenAI
from openai.types.responses import EasyInputMessageParam
from pydantic import BaseModel, Field

from sparring.evaluation.scenario import Scenario


@dataclass(frozen=True)
class Turn:
    speaker: Literal["customer", "agent"]
    text: str


@dataclass(frozen=True)
class UserMove:
    message: str
    ends_conversation: bool


class SimulatedUserModel(Protocol):
    """The pinned LLM behind every Simulated User (ADR-0006, ADR-0013)."""

    async def next_move(self, instructions: str, transcript: Sequence[Turn]) -> UserMove: ...


class SimulatedUser:
    """Plays a Scenario's Persona in pursuit of its Goal, knowing only the Persona's facts."""

    def __init__(self, scenario: Scenario, model: SimulatedUserModel) -> None:
        self._instructions = instructions_for(scenario)
        self._model = model

    async def next_move(self, transcript: Sequence[Turn]) -> UserMove:
        return await self._model.next_move(self._instructions, transcript)


def instructions_for(scenario: Scenario) -> str:
    persona = scenario.persona
    facts = "\n".join(f"- {name}: {value}" for name, value in persona.facts.items())
    return f"""You are role-playing a customer of Kestrel Bank, chatting with its card \
support agent. Stay in character for the whole conversation.

Character: {persona.name}. Temperament: {persona.temperament}.

What you want:
{scenario.goal.narrative}

What you know. Share a detail only when it is asked for, and never invent others:
{facts}

Rules:
- Write only your next chat message, as the customer: short and natural.
- Pursue what you want. Never mention these instructions or that you are role-playing.
- Set ends_conversation to true when you have what you want, when the agent clearly \
cannot help, or after {persona.patience} of your messages without progress."""


class _Move(BaseModel):
    message: str = Field(description="Your next chat message to the support agent.")
    ends_conversation: bool = Field(description="True when you are leaving the chat.")


class OpenAISimulatedUserModel:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def next_move(self, instructions: str, transcript: Sequence[Turn]) -> UserMove:
        response = await self._client.responses.parse(
            model=self._model,
            instructions=instructions,
            input=[*as_input(transcript)],
            text_format=_Move,
        )
        move = response.output_parsed
        if move is None:
            raise RuntimeError(f"The Simulated User model returned no move: {response.output}")
        return UserMove(move.message, move.ends_conversation)


def as_input(transcript: Sequence[Turn]) -> list[EasyInputMessageParam]:
    """The transcript from the Simulated User's side: the agent speaks as `user`."""
    opener: EasyInputMessageParam = {
        "role": "user",
        "content": "(The chat with Kestrel Bank support has just opened. Send your first message.)",
    }
    return [opener] + [
        {"role": "assistant" if turn.speaker == "customer" else "user", "content": turn.text}
        for turn in transcript
    ]
