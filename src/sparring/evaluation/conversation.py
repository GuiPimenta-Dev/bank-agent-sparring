import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from typing import Protocol

from sparring.evaluation.cost import Usage
from sparring.evaluation.failure_signature import failure_signature
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.invariants import InvariantViolation, invariant_violations
from sparring.evaluation.judges import ToneJudge, ToneJudgement
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.recording import Recorder
from sparring.evaluation.scenario import BankSnapshot, Scenario
from sparring.evaluation.simulated_user import SimulatedUser, SimulatedUserModel, Turn


@dataclass(frozen=True)
class ConfirmationRequest:
    id: str
    summary: str


class SystemUnderTest(Protocol):
    """The agent and the bank behind it, freshly set up for one Scenario."""

    async def reply(self, message: str) -> Turn:
        """The agent's turn: its reply and the tool calls it requested.

        Raises `InfrastructureFailure` when the agent's provider still fails after retries.
        """
        ...

    async def confirmation_requests(self) -> list[ConfirmationRequest]:
        """What the Persona's Customer Channel shows right now."""
        ...

    async def answer(self, request_id: str, confirm: bool) -> None:
        """Confirm or reject a request through the Customer Channel, never the agent."""
        ...

    def bank_snapshot(self) -> BankSnapshot: ...


SystemFactory = Callable[[Scenario, str], AbstractAsyncContextManager[SystemUnderTest]]
"""Sets up the system under test for a Scenario and a conversation id."""


@dataclass(frozen=True)
class Conversation:
    id: str
    scenario: Scenario
    transcript: list[Turn]
    final: BankSnapshot
    reached_turn_limit: bool
    interrupted_by: str | None = None
    """The infrastructure failure that cut the conversation short, if one did."""
    trace_id: str | None = None
    """Where the conversation was recorded."""
    judge_usage: tuple[Usage, ...] = ()

    @property
    def usage(self) -> list[Usage]:
        """What the agent and the Simulated User consumed, turn by turn, and then the judges."""
        turns = [turn.usage for turn in self.transcript if turn.usage is not None]
        return [*turns, *self.judge_usage]

    @property
    def hard_rule_refusals(self) -> list[str]:
        """The Hard Rules that refused the agent's tool calls, in order."""
        return [
            call.refused_by
            for turn in self.transcript
            for call in turn.tool_calls
            if call.refused_by is not None
        ]


@dataclass(frozen=True)
class Verdict:
    """Inconclusive if infrastructure cut the conversation short; otherwise a Failure if the
    final Bank Snapshot differs from the Expected Outcome or an Invariant was violated."""

    inconclusive: bool
    differences: list[str]
    violations: list[InvariantViolation]
    signature: str | None = None
    """The Failure Signature, on a Failure only."""
    tone: ToneJudgement | None = None
    """The tone judge's view, which never decides a pass or a Failure (ADR-0002)."""

    @property
    def failed(self) -> bool:
        return not self.inconclusive and bool(self.differences or self.violations)

    @property
    def passed(self) -> bool:
        return not self.inconclusive and not self.failed


class ScenarioRunner:
    """Plays a Scenario between a Simulated User and the system under test, judges the
    conversation with the Oracle and the Invariants, and records it."""

    def __init__(
        self,
        open_system: SystemFactory,
        simulator: SimulatedUserModel,
        oracle: Oracle,
        recorder: Recorder,
        tone_judge: ToneJudge | None = None,
        turn_limit: int = 20,
    ) -> None:
        self._open_system = open_system
        self._simulator = simulator
        self._oracle = oracle
        self._recorder = recorder
        self._tone_judge = tone_judge
        self._turn_limit = turn_limit

    async def run(self, scenario: Scenario) -> tuple[Conversation, Verdict]:
        conversation_id = f"{scenario.id}-{uuid.uuid4().hex[:8]}"
        async with (
            self._recorder.conversation(scenario, conversation_id) as trace_id,
            self._open_system(scenario, conversation_id) as system,
        ):
            conversation = await self._play(conversation_id, scenario, system, trace_id)
            verdict = self._judge(conversation)
            tone = await self._tone(conversation) if not verdict.inconclusive else None
            if tone is not None:
                verdict = replace(verdict, tone=tone)
                conversation = replace(conversation, judge_usage=(*_used(tone),))
            self._recorder.record(conversation, verdict)
        return conversation, verdict

    async def _tone(self, conversation: Conversation) -> ToneJudgement | None:
        """No tone score rather than no conversation when the judge's provider is down."""
        if self._tone_judge is None:
            return None
        try:
            return await self._tone_judge.judge(conversation.transcript)
        except InfrastructureFailure:
            return None

    def _judge(self, conversation: Conversation) -> Verdict:
        if conversation.interrupted_by is not None:
            return Verdict(inconclusive=True, differences=[], violations=[])
        scenario = conversation.scenario
        differences = self._oracle.compare(scenario, conversation.final)
        violations = invariant_violations(conversation.transcript)
        failed = bool(differences or violations)
        return Verdict(
            inconclusive=False,
            differences=[str(difference) for difference in differences],
            violations=violations,
            signature=failure_signature(
                scenario.goal.intent.operation, differences, violations, scenario.tactic
            )
            if failed
            else None,
        )

    async def _play(
        self,
        conversation_id: str,
        scenario: Scenario,
        system: SystemUnderTest,
        trace_id: str | None,
    ) -> Conversation:
        transcript: list[Turn] = []
        try:
            reached_turn_limit = await self._exchange(scenario, system, transcript)
        except InfrastructureFailure as failure:
            return Conversation(
                conversation_id,
                scenario,
                transcript,
                system.bank_snapshot(),
                reached_turn_limit=False,
                interrupted_by=str(failure),
                trace_id=trace_id,
            )
        return Conversation(
            conversation_id,
            scenario,
            transcript,
            system.bank_snapshot(),
            reached_turn_limit,
            trace_id=trace_id,
        )

    async def _exchange(
        self, scenario: Scenario, system: SystemUnderTest, transcript: list[Turn]
    ) -> bool:
        """Plays turns into `transcript` until the Simulated User ends the conversation, the
        agent hands it to a human (ES-1), or the turn limit, a safety stop, is reached.
        Turns land in `transcript` as they happen, so an interrupted conversation keeps them.
        Returns whether the turn limit was reached."""
        user = SimulatedUser(scenario, self._simulator)
        shown: set[str] = set()
        for _ in range(self._turn_limit):
            requests = {request.id: request for request in await system.confirmation_requests()}
            for request in requests.values():
                if request.id not in shown:
                    shown.add(request.id)
                    transcript.append(
                        Turn("customer_channel", f"Request {request.id}: {request.summary}")
                    )
            move = await user.next_move(transcript)
            for request_id, confirm in move.decisions.items():
                if request_id in requests:
                    await system.answer(request_id, confirm)
                    verb = "confirmed" if confirm else "rejected"
                    transcript.append(Turn("customer_channel", f"You {verb} request {request_id}."))
            transcript.append(Turn("customer", move.message, usage=move.usage))
            if move.ends_conversation:
                return False
            transcript.append(await system.reply(move.message))
            if system.bank_snapshot().escalated:
                return False
        return True


def _used(judgement: ToneJudgement) -> list[Usage]:
    return [judgement.usage] if judgement.usage else []
