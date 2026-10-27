"""The story of a generated Scenario: the one part an LLM writes (ADR-0001).

A story may only repeat details from the Persona's facts. One that cites any other number
or id would give the Simulated User something the Bank Snapshot does not back, so it is
discarded before the conversation runs.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import openai
from openai import AsyncOpenAI

from sparring.evaluation.cost import Usage
from sparring.evaluation.dimensions import ScenarioDraft
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.simulated_user import retries_ran_out, usage_of

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
ID = re.compile(r"\b(?:tx|card|pa|esc|dispute)-[\w-]+|\bC-\d+")

INSTRUCTIONS = """You write the background of a role-play for testing a bank's card \
support agent. In two to four sentences, in the second person ("You..."), tell the \
customer what happened before they opened the chat and how they feel about it.

Use only the details listed. Never add a number, amount, date, card number or id that is \
not listed, and write the listed ones exactly as given. Do not say what the customer \
should ask for or how they should talk to the agent; that is given to them separately."""


@dataclass(frozen=True)
class Story:
    text: str
    usage: Usage | None


class NarrativeModel(Protocol):
    """The pinned LLM that writes generated Scenarios' stories (ADR-0006, ADR-0013).

    Raises `InfrastructureFailure` when its provider still fails after retries.
    """

    async def write(self, instructions: str, brief: str) -> Story: ...


def brief(draft: ScenarioDraft) -> str:
    facts = "\n".join(f"- {name}: {value}" for name, value in draft.persona.facts.items())
    return (
        f"The customer: {draft.persona.name}, {draft.persona.temperament}.\n"
        f"What they want: {draft.wants}\n"
        f"Details they know:\n{facts}"
    )


def uncited_details(story: str, facts: Mapping[str, str]) -> list[str]:
    """The numbers and ids in `story` that the facts do not contain. Numbers compare by
    value, so "£1,200.00" in the facts covers "1200" in the story."""
    known = " ".join(facts.values())
    numbers = {_value(number) for number in NUMBER.findall(known)}
    uncited = [number for number in NUMBER.findall(story) if _value(number) not in numbers]
    return uncited + [found for found in ID.findall(story) if found not in known]


def _value(number: str) -> Decimal:
    return Decimal(number.replace(",", ""))


class OpenAINarrativeModel:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def write(self, instructions: str, brief: str) -> Story:
        try:
            response = await self._client.responses.create(
                model=self._model, instructions=instructions, input=brief
            )
        except openai.APIError as error:
            if retries_ran_out(error):
                raise InfrastructureFailure(str(error)) from error
            raise
        return Story(response.output_text, usage_of(response, self._model))
