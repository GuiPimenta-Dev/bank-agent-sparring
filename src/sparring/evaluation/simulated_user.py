from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import openai
from openai import AsyncOpenAI
from openai.types.responses import EasyInputMessageParam, Response
from pydantic import BaseModel, Field

from sparring.evaluation.cost import Usage
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.scenario import Scenario


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any]
    refused_by: str | None = None
    """The Hard Rule that refused the call, if one did."""


@dataclass(frozen=True)
class Turn:
    """One line of a conversation. `customer_channel` lines are what the Customer sees and
    does in the Kestrel Bank app, outside the chat with the agent. An agent's turn also
    carries the tool calls it requested, which the Simulated User never sees."""

    speaker: Literal["customer", "agent", "customer_channel"]
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage | None = None
    """What the model behind the speaker consumed to produce this turn."""


@dataclass(frozen=True)
class UserMove:
    message: str
    ends_conversation: bool
    decisions: Mapping[str, bool] = field(default_factory=dict[str, bool])
    """Confirm (True) or reject (False) requests shown in the app, by request id."""
    usage: Usage | None = None


class SimulatedUserModel(Protocol):
    """The pinned LLM behind every Simulated User (ADR-0006, ADR-0013).

    Raises `InfrastructureFailure` when its provider still fails after retries.
    """

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
- Ask to speak to a human only if what you want says so.
- Set ends_conversation to true when you have what you want, when the agent clearly \
cannot help, or after {persona.patience} of your messages without progress.
- Your Kestrel Bank app may show a request to confirm. Decide as your character would \
and put the decision in `decisions` with the request's id; a careful person confirms \
only what they actually want. Leave `decisions` empty when there is nothing to decide."""


class _Decision(BaseModel):
    request_id: str
    confirm: bool


class _Move(BaseModel):
    message: str = Field(description="Your next chat message to the support agent.")
    ends_conversation: bool = Field(description="True when you are leaving the chat.")
    decisions: list[_Decision] = Field(description="Your answers to requests in your app.")


class OpenAISimulatedUserModel:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def next_move(self, instructions: str, transcript: Sequence[Turn]) -> UserMove:
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=instructions,
                input=[*as_input(transcript)],
                text_format=_Move,
            )
        except openai.APIError as error:
            if retries_ran_out(error):
                raise InfrastructureFailure(str(error)) from error
            raise
        move = response.output_parsed
        if move is None:
            raise RuntimeError(f"The Simulated User model returned no move: {response.output}")
        decisions = {decision.request_id: decision.confirm for decision in move.decisions}
        return UserMove(
            move.message, move.ends_conversation, decisions, usage_of(response, self._model)
        )


def usage_of(response: Response, model: str) -> Usage | None:
    """Priced as the model that was asked for, since the response may name a dated snapshot.
    OpenAI counts cached input inside `input_tokens`; it is taken out again here."""
    if response.usage is None:
        return None
    cached = response.usage.input_tokens_details.cached_tokens
    return Usage(
        model,
        input_tokens=response.usage.input_tokens - cached,
        output_tokens=response.usage.output_tokens,
        cache_read_tokens=cached,
    )


def retries_ran_out(error: openai.APIError) -> bool:
    """Whether the error is one the SDK retries, so it was raised only once retries ran out."""
    if isinstance(error, openai.APIStatusError):
        return error.status_code in (408, 409, 429) or error.status_code >= 500
    return isinstance(error, openai.APIConnectionError)


def as_input(transcript: Sequence[Turn]) -> list[EasyInputMessageParam]:
    """The transcript from the Simulated User's side: the agent speaks as `user`."""
    opener: EasyInputMessageParam = {
        "role": "user",
        "content": "(The chat with Kestrel Bank support has just opened. Send your first message.)",
    }
    return [opener] + [_as_message(turn) for turn in transcript]


def _as_message(turn: Turn) -> EasyInputMessageParam:
    match turn.speaker:
        case "customer":
            return {"role": "assistant", "content": turn.text}
        case "agent":
            return {"role": "user", "content": turn.text}
        case "customer_channel":
            return {"role": "user", "content": f"[Your Kestrel Bank app] {turn.text}"}
