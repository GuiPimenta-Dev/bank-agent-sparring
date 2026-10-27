import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from sparring.evaluation.oracle import Oracle, Verdict
from sparring.evaluation.recording import Recorder
from sparring.evaluation.scenario import BankSnapshot, Scenario
from sparring.evaluation.simulated_user import SimulatedUser, SimulatedUserModel, Turn


@dataclass(frozen=True)
class ConfirmationRequest:
    id: str
    summary: str


class SystemUnderTest(Protocol):
    """The agent and the bank behind it, freshly set up for one Scenario."""

    async def reply(self, message: str) -> str: ...

    async def confirmation_requests(self) -> list[ConfirmationRequest]:
        """What the Persona's Customer Channel shows right now."""
        ...

    async def answer(self, request_id: str, confirm: bool) -> None:
        """Confirm or reject a request through the Customer Channel, never the agent."""
        ...

    def bank_snapshot(self) -> BankSnapshot: ...

    def hard_rule_refusals(self) -> list[str]:
        """The Hard Rules that refused the agent's tool calls so far, in order."""
        ...


SystemFactory = Callable[[Scenario, str], AbstractAsyncContextManager[SystemUnderTest]]
"""Sets up the system under test for a Scenario and a conversation id."""


@dataclass(frozen=True)
class Conversation:
    id: str
    scenario: Scenario
    transcript: list[Turn]
    final: BankSnapshot
    reached_turn_limit: bool
    hard_rule_refusals: list[str]


class ScenarioRunner:
    """Plays a Scenario between a Simulated User and the system under test, then judges
    the final Bank Snapshot with the Oracle and records the conversation."""

    def __init__(
        self,
        open_system: SystemFactory,
        simulator: SimulatedUserModel,
        oracle: Oracle,
        recorder: Recorder,
        turn_limit: int = 20,
    ) -> None:
        self._open_system = open_system
        self._simulator = simulator
        self._oracle = oracle
        self._recorder = recorder
        self._turn_limit = turn_limit

    async def run(self, scenario: Scenario) -> tuple[Conversation, Verdict]:
        conversation_id = f"{scenario.id}-{uuid.uuid4().hex[:8]}"
        async with (
            self._recorder.conversation(scenario, conversation_id),
            self._open_system(scenario, conversation_id) as system,
        ):
            conversation = await self._play(conversation_id, scenario, system)
            verdict = self._oracle.judge(scenario, conversation.final)
            self._recorder.record(conversation, verdict)
        return conversation, verdict

    async def _play(
        self, conversation_id: str, scenario: Scenario, system: SystemUnderTest
    ) -> Conversation:
        """The Simulated User ends the conversation, unless the agent hands it to a human
        first (ES-1); the turn limit is only a safety stop."""
        user = SimulatedUser(scenario, self._simulator)
        transcript: list[Turn] = []
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
            transcript.append(Turn("customer", move.message))
            if move.ends_conversation:
                return self._ended(conversation_id, scenario, transcript, system, False)
            transcript.append(Turn("agent", await system.reply(move.message)))
            if system.bank_snapshot().escalated:
                return self._ended(conversation_id, scenario, transcript, system, False)
        return self._ended(conversation_id, scenario, transcript, system, True)

    @staticmethod
    def _ended(
        conversation_id: str,
        scenario: Scenario,
        transcript: list[Turn],
        system: SystemUnderTest,
        reached_turn_limit: bool,
    ) -> Conversation:
        return Conversation(
            conversation_id,
            scenario,
            transcript,
            system.bank_snapshot(),
            reached_turn_limit,
            system.hard_rule_refusals(),
        )
