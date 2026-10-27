import asyncio
from collections.abc import Sequence
from decimal import Decimal

import pytest
from fakes import says, scripted
from mcp import Client

from sparring.agent.support_agent import SupportAgent
from sparring.evaluation.conversation import ScenarioRunner
from sparring.evaluation.cost import Budget, Price, PriceTable, Usage
from sparring.evaluation.exploration import Exploration
from sparring.evaluation.golden_set import InMemoryGoldenSet
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.judges import DriftJudgement
from sparring.evaluation.narrative import NarrativeModel, Story
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.promotion import Promotion
from sparring.evaluation.quarantine import InMemoryQuarantine
from sparring.evaluation.recording import InMemoryRecorder
from sparring.evaluation.simulated_user import Turn, UserMove
from sparring.system_under_test import kestrel

pytestmark = pytest.mark.anyio

ZERO = Decimal(0)
PRICES = PriceTable(
    {
        "scripted": Price(ZERO, ZERO, ZERO, ZERO),
        "simulator": Price(Decimal(1), Decimal(5), ZERO, ZERO),
    }
)
STORY_COST = Decimal("0.01")
MOVE_COST = Decimal("0.05")


class Narrator:
    """Writes the same story every time, at $0.01 a story, and keeps how many it was
    writing at once."""

    def __init__(self, story: str = "You are cross about it.") -> None:
        self._story = story
        self.writing = 0
        self.most_at_once = 0

    async def write(self, instructions: str, brief: str) -> Story:
        self.writing += 1
        self.most_at_once = max(self.most_at_once, self.writing)
        await asyncio.sleep(0.01)
        self.writing -= 1
        return Story(self._story, Usage("simulator", input_tokens=10_000))


class Unbiased:
    """A Policy Drift judge that never suspects drift."""

    async def judge(self, transcript: Sequence[Turn], findings: Sequence[str]) -> DriftJudgement:
        return DriftJudgement(False, "The agent broke a rule.")


class LeavingUser:
    """A Simulated User who leaves at once, at $0.05 a move."""

    def __init__(self) -> None:
        self.moves = 0

    async def next_move(self, instructions: str, transcript: Sequence[Turn]) -> UserMove:
        self.moves += 1
        return UserMove("Never mind, bye.", True, usage=Usage("simulator", output_tokens=10_000))


def exploration(
    narrator: NarrativeModel,
    user: LeavingUser | None = None,
    parallelism: int = 1,
    recorder: InMemoryRecorder | None = None,
    golden_set: InMemoryGoldenSet | None = None,
) -> Exploration:
    async def connect_agent(client: Client) -> SupportAgent:
        return await SupportAgent.with_model(client, scripted(says("How can I help?")))

    recorder = recorder or InMemoryRecorder()
    runner = ScenarioRunner(
        kestrel(connect_agent, "scripted"), user or LeavingUser(), Oracle(), recorder
    )
    promotion = Promotion(
        golden_set or InMemoryGoldenSet(), Oracle(), Unbiased(), InMemoryQuarantine(), recorder
    )
    return Exploration(runner, narrator, promotion, PRICES, parallelism)


async def test_the_budget_stops_launching_conversations_before_the_cap() -> None:
    budget = Budget(cap=Decimal("0.50"), estimate=Decimal("0.10"))

    report = await exploration(Narrator()).run("run", 20, budget, seed=1)

    assert report.explored and report.not_launched
    assert report.spent <= report.cap
    assert sum(e.cost for e in report.explored) == report.spent


async def test_a_conversation_costs_its_story_its_moves_and_its_reproductions() -> None:
    budget = Budget(cap=Decimal(10), estimate=Decimal("0.10"))

    report = await exploration(Narrator()).run("run", 10, budget, seed=1)

    for explored in report.explored:
        reproductions = 1 if explored.promotion == "promoted" else 0
        assert explored.cost == STORY_COST + MOVE_COST * (1 + reproductions)


async def test_conversations_run_concurrently_but_never_more_than_the_parallelism() -> None:
    narrator = Narrator()
    budget = Budget(cap=Decimal(10), estimate=Decimal("0.10"))

    report = await exploration(narrator, parallelism=2).run("run", 6, budget, seed=2)

    assert len(report.explored) == 6
    assert narrator.most_at_once == 2


async def test_a_story_citing_details_not_in_the_facts_is_discarded_before_running() -> None:
    user = LeavingUser()
    budget = Budget(cap=Decimal(10), estimate=Decimal("0.10"))

    report = await exploration(Narrator("It cost you £9,999.99."), user).run("run", 3, budget, 3)

    assert [e.outcome for e in report.explored] == ["discarded"] * 3
    assert all("9,999.99" in e.findings[0] for e in report.explored)
    assert [e.cost for e in report.explored] == [2 * STORY_COST] * 3, "two attempts each"
    assert user.moves == 0


class UnreachableNarrator:
    async def write(self, instructions: str, brief: str) -> Story:
        raise InfrastructureFailure("OpenAI is unreachable")


async def test_a_story_writer_outage_makes_the_conversation_inconclusive() -> None:
    budget = Budget(cap=Decimal(10), estimate=Decimal("0.10"))

    report = await exploration(UnreachableNarrator()).run("run", 2, budget, seed=4)

    assert [e.outcome for e in report.explored] == ["inconclusive"] * 2
    assert report.spent == 0


async def test_the_report_counts_outcomes_and_the_cost_per_resolved_conversation() -> None:
    recorder = InMemoryRecorder()
    budget = Budget(cap=Decimal(10), estimate=Decimal("0.10"))

    report = await exploration(Narrator(), recorder=recorder).run("run", 20, budget, seed=5)

    passes, failures = report.count("pass"), report.count("failure")
    assert passes and failures and passes + failures == 20
    assert report.cost_per_resolved_conversation == report.spent / passes
    for explored in report.explored:
        if explored.outcome == "failure":
            assert explored.findings and explored.promotion
    traced = {conversation.scenario.id: conversation for conversation, _ in recorder.records}
    for explored in report.explored:
        assert traced[explored.scenario_id].scenario.dimensions == explored.dimensions


async def test_failures_found_by_exploration_grow_the_golden_set() -> None:
    golden_set = InMemoryGoldenSet()
    budget = Budget(cap=Decimal(10), estimate=Decimal("0.10"))

    report = await exploration(Narrator(), golden_set=golden_set).run("run", 20, budget, seed=5)

    promoted = {e.scenario_id for e in report.explored if e.promotion == "promoted"}
    assert promoted == {item.scenario.id for item in await golden_set.items()}
