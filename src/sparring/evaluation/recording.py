from collections.abc import AsyncGenerator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Protocol

from langfuse import Langfuse, propagate_attributes

from sparring.evaluation.scenario import Scenario

if TYPE_CHECKING:
    from sparring.evaluation.conversation import Conversation
    from sparring.evaluation.oracle import Verdict


class Recorder(Protocol):
    """Where conversations and their Verdicts are kept (ADR-0012)."""

    def conversation(
        self, scenario: Scenario, conversation_id: str
    ) -> AbstractAsyncContextManager[None]:
        """Everything that happens inside belongs to this conversation."""
        ...

    def record(self, conversation: "Conversation", verdict: "Verdict") -> None: ...


class LangfuseRecorder:
    """One Langfuse trace per conversation; the agent's turns nest inside it.

    Each Hard Rule refusal is a categorical score named after the rule, so refusals
    can be counted per rule across conversations (ADR-0017).
    """

    def __init__(self, langfuse: Langfuse, metadata: Mapping[str, str]) -> None:
        self._langfuse = langfuse
        self._metadata = metadata

    @asynccontextmanager
    async def conversation(self, scenario: Scenario, conversation_id: str) -> AsyncGenerator[None]:
        metadata = {**self._metadata, **scenario.dimensions, "scenario_id": scenario.id}
        with (
            self._langfuse.start_as_current_observation(name="conversation", as_type="span"),
            propagate_attributes(
                session_id=conversation_id,
                trace_name=f"scenario {scenario.id}",
                tags=["scenario"],
                metadata=metadata,
            ),
        ):
            yield

    def record(self, conversation: "Conversation", verdict: "Verdict") -> None:
        self._langfuse.update_current_span(
            output={
                "transcript": [asdict(turn) for turn in conversation.transcript],
                "differences": verdict.differences,
                "reached_turn_limit": conversation.reached_turn_limit,
                "hard_rule_refusals": conversation.hard_rule_refusals,
            }
        )
        self._langfuse.score_current_trace(
            name="oracle_pass", value=1 if verdict.passed else 0, data_type="BOOLEAN"
        )
        for rule in conversation.hard_rule_refusals:
            self._langfuse.score_current_trace(
                name="hard_rule_refusal", value=rule, data_type="CATEGORICAL"
            )


class InMemoryRecorder:
    def __init__(self) -> None:
        self.records: list[tuple[Conversation, Verdict]] = []

    @asynccontextmanager
    async def conversation(self, scenario: Scenario, conversation_id: str) -> AsyncGenerator[None]:
        yield

    def record(self, conversation: "Conversation", verdict: "Verdict") -> None:
        self.records.append((conversation, verdict))
