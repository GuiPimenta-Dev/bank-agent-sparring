"""LLM judges: signal the Oracle and the Invariants cannot capture.

A judge may flag a conversation or block a Promotion, never cause one (ADR-0002). Judges run
on the Simulated User's pinned model, never the agent's (ADR-0006).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from sparring.evaluation.cost import Usage
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.simulated_user import Turn, retries_ran_out, usage_of


@dataclass(frozen=True)
class DriftJudgement:
    suspected: bool
    """Whether the Policy text justifies what the Oracle or an Invariant called a Failure."""
    reasoning: str
    usage: Usage | None = None


@dataclass(frozen=True)
class ToneJudgement:
    score: int
    """From 1 (rude, cold or confusing) to 5 (warm, clear and brief)."""
    reasoning: str
    usage: Usage | None = None


class PolicyDriftJudge(Protocol):
    """Asks whether a Failure is the agent's mistake or a gap between the Policy text and
    the Oracle. Raises `InfrastructureFailure` when its provider still fails after retries."""

    async def judge(
        self, transcript: Sequence[Turn], findings: Sequence[str]
    ) -> DriftJudgement: ...


class ToneJudge(Protocol):
    """Raises `InfrastructureFailure` when its provider still fails after retries."""

    async def judge(self, transcript: Sequence[Turn]) -> ToneJudgement: ...


DRIFT_INSTRUCTIONS = """You review a chat between Kestrel Bank's card support agent and a \
customer. An automatic checker, which implements the bank's Policy as code, says the agent \
got something wrong; its findings are listed. The Policy text the agent was given is below.

Decide whether the agent's behaviour is nevertheless justified by the Policy text: whether \
a careful reader of that text, knowing only what the customer said, could reasonably have \
acted as the agent did. Answer justified = true only when the text itself supports the \
agent, for example because it is ambiguous or silent on the point. If the agent simply \
broke a rule, misread the customer or did not finish the job, answer false.

The Policy text:

{policy}"""

TONE_INSTRUCTIONS = """You rate how a bank's card support agent spoke to a customer, from 1 \
to 5: 1 is rude, cold or confusing; 3 is correct but stiff or wordy; 5 is warm, clear, \
brief and in plain English. Judge only the agent's tone and clarity, not whether it did the \
right thing."""


def as_text(transcript: Sequence[Turn]) -> str:
    """The transcript as a judge reads it, with the agent's tool calls shown."""
    lines: list[str] = []
    for turn in transcript:
        match turn.speaker:
            case "customer":
                lines.append(f"Customer: {turn.text}")
            case "customer_channel":
                lines.append(f"[Customer's app] {turn.text}")
            case "agent":
                for call in turn.tool_calls:
                    refused = f" -> refused: {call.refused_by}" if call.refused_by else ""
                    lines.append(f"  (tool) {call.name}({dict(call.arguments)}){refused}")
                lines.append(f"Agent: {turn.text}")
    return "\n".join(lines)


class _Drift(BaseModel):
    reasoning: str = Field(description="Why, in two or three sentences.")
    justified: bool = Field(description="True when the Policy text supports the agent.")


class _Tone(BaseModel):
    reasoning: str = Field(description="Why, in one or two sentences.")
    score: int = Field(ge=1, le=5)


class OpenAIPolicyDriftJudge:
    def __init__(self, client: AsyncOpenAI, model: str, policy: str) -> None:
        self._client = client
        self._model = model
        self._instructions = DRIFT_INSTRUCTIONS.format(policy=policy)

    async def judge(self, transcript: Sequence[Turn], findings: Sequence[str]) -> DriftJudgement:
        listed = "\n".join(f"- {finding}" for finding in findings)
        brief = f"The checker's findings:\n{listed}\n\nThe chat:\n{as_text(transcript)}"
        answer, usage = await _ask(self._client, self._model, self._instructions, brief, _Drift)
        return DriftJudgement(answer.justified, answer.reasoning, usage)


class OpenAIToneJudge:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def judge(self, transcript: Sequence[Turn]) -> ToneJudgement:
        answer, usage = await _ask(
            self._client, self._model, TONE_INSTRUCTIONS, as_text(transcript), _Tone
        )
        return ToneJudgement(answer.score, answer.reasoning, usage)


async def _ask[Answer: BaseModel](
    client: AsyncOpenAI, model: str, instructions: str, brief: str, answer: type[Answer]
) -> tuple[Answer, Usage | None]:
    try:
        response = await client.responses.parse(
            model=model, instructions=instructions, input=brief, text_format=answer
        )
    except openai.APIError as error:
        if retries_ran_out(error):
            raise InfrastructureFailure(str(error)) from error
        raise
    if response.output_parsed is None:
        raise RuntimeError(f"The judge returned no answer: {response.output}")
    return response.output_parsed, usage_of(response, model)
