import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from sparring.evaluation.oracle import Oracle, Verdict
from sparring.evaluation.recording import Recorder
from sparring.evaluation.scenario import BankSnapshot, Scenario
from sparring.evaluation.simulated_user import SimulatedUser, SimulatedUserModel, Turn


class SystemUnderTest(Protocol):
    """The agent and the bank behind it, freshly set up for one Scenario."""

    async def reply(self, message: str) -> str: ...

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
        """The Simulated User ends the conversation; the turn limit is only a safety stop."""
        user = SimulatedUser(scenario, self._simulator)
        transcript: list[Turn] = []
        for _ in range(self._turn_limit):
            move = await user.next_move(transcript)
            transcript.append(Turn("customer", move.message))
            if move.ends_conversation:
                return Conversation(
                    conversation_id, scenario, transcript, system.bank_snapshot(), False
                )
            transcript.append(Turn("agent", await system.reply(move.message)))
        return Conversation(conversation_id, scenario, transcript, system.bank_snapshot(), True)
