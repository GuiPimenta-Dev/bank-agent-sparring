from collections.abc import AsyncGenerator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Protocol

from langfuse import Langfuse, propagate_attributes

from sparring.evaluation.scenario import Scenario, scenario_to_json

if TYPE_CHECKING:
    from sparring.evaluation.conversation import Conversation, Verdict
    from sparring.evaluation.judges import DriftJudgement


class Recorder(Protocol):
    """Where conversations and their Verdicts are kept (ADR-0012)."""

    def conversation(
        self, scenario: Scenario, conversation_id: str
    ) -> AbstractAsyncContextManager[str | None]:
        """Everything that happens inside belongs to this conversation, recorded under the
        trace id it yields."""
        ...

    def record(self, conversation: "Conversation", verdict: "Verdict") -> None: ...

    def promotion_decided(self, trace_id: str | None, decision: str) -> None:
        """What Promotion decided about the Failure recorded under `trace_id`."""
        ...

    def drift_judged(self, trace_id: str | None, judgement: "DriftJudgement") -> None:
        """What the Policy Drift judge said about the Failure recorded under `trace_id`."""
        ...


class LangfuseRecorder:
    """One Langfuse trace per conversation; the agent's turns nest inside it.

    Every conversation gets an `inconclusive` score, so its mean is the Inconclusive rate;
    only conclusive ones get `oracle_pass` (ADR-0017). Each Hard Rule refusal and each
    Invariant violation is a categorical score named after the rule or Invariant, and every
    Failure a `failure_signature`, so they can be counted across conversations (ADR-0012).
    The LLM judges' `tone` and `drift_suspected` carry their reasoning as the comment.
    Every conclusive conversation also gets a `failure` score carrying its Scenario's
    Dimension values, the input of Adaptive Sampling.
    """

    def __init__(self, langfuse: Langfuse, metadata: Mapping[str, str]) -> None:
        self._langfuse = langfuse
        self._metadata = metadata

    @asynccontextmanager
    async def conversation(
        self, scenario: Scenario, conversation_id: str
    ) -> AsyncGenerator[str | None]:
        metadata = {**self._metadata, **scenario.dimensions, "scenario_id": scenario.id}
        with (
            self._langfuse.start_as_current_observation(
                name="conversation", as_type="span", input={"scenario": scenario_to_json(scenario)}
            ),
            propagate_attributes(
                session_id=conversation_id,
                trace_name=f"scenario {scenario.id}",
                tags=["scenario"],
                metadata=metadata,
            ),
        ):
            yield self._langfuse.get_current_trace_id()

    def record(self, conversation: "Conversation", verdict: "Verdict") -> None:
        self._langfuse.update_current_span(
            output={
                "transcript": [asdict(turn) for turn in conversation.transcript],
                "differences": verdict.differences,
                "invariant_violations": [asdict(v) for v in verdict.violations],
                "interrupted_by": conversation.interrupted_by,
                "reached_turn_limit": conversation.reached_turn_limit,
                "hard_rule_refusals": conversation.hard_rule_refusals,
                "escalated": conversation.final.escalated,
            }
        )
        self._langfuse.score_current_trace(
            name="inconclusive", value=1 if verdict.inconclusive else 0, data_type="BOOLEAN"
        )
        if not verdict.inconclusive:
            self._langfuse.score_current_trace(
                name="oracle_pass", value=0 if verdict.differences else 1, data_type="BOOLEAN"
            )
            self._langfuse.score_current_trace(
                name="failure",
                value=1 if verdict.failed else 0,
                data_type="BOOLEAN",
                metadata=dict(conversation.scenario.dimensions),
            )
        for rule in conversation.hard_rule_refusals:
            self._langfuse.score_current_trace(
                name="hard_rule_refusal", value=rule, data_type="CATEGORICAL"
            )
        for violation in verdict.violations:
            self._langfuse.score_current_trace(
                name="invariant_violation", value=violation.invariant, data_type="CATEGORICAL"
            )
        if verdict.signature is not None:
            self._langfuse.score_current_trace(
                name="failure_signature", value=verdict.signature, data_type="CATEGORICAL"
            )
        if verdict.tone is not None:
            self._langfuse.score_current_trace(
                name="tone",
                value=verdict.tone.score,
                data_type="NUMERIC",
                comment=verdict.tone.reasoning,
            )

    def promotion_decided(self, trace_id: str | None, decision: str) -> None:
        self._langfuse.create_score(
            trace_id=trace_id, name="promotion", value=decision, data_type="CATEGORICAL"
        )

    def drift_judged(self, trace_id: str | None, judgement: "DriftJudgement") -> None:
        self._langfuse.create_score(
            trace_id=trace_id,
            name="drift_suspected",
            value=1 if judgement.suspected else 0,
            data_type="BOOLEAN",
            comment=judgement.reasoning,
        )


class InMemoryRecorder:
    def __init__(self) -> None:
        self.records: list[tuple[Conversation, Verdict]] = []
        self.promotions: dict[str | None, str] = {}
        self.drift: dict[str | None, DriftJudgement] = {}

    @asynccontextmanager
    async def conversation(
        self, scenario: Scenario, conversation_id: str
    ) -> AsyncGenerator[str | None]:
        yield f"trace-{conversation_id}"

    def record(self, conversation: "Conversation", verdict: "Verdict") -> None:
        self.records.append((conversation, verdict))

    def promotion_decided(self, trace_id: str | None, decision: str) -> None:
        self.promotions[trace_id] = decision

    def drift_judged(self, trace_id: str | None, judgement: "DriftJudgement") -> None:
        self.drift[trace_id] = judgement
