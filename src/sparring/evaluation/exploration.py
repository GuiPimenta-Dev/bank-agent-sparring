"""Exploration: the scheduled search for new Failures, under a hard dollar cap (ADR-0020)."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from random import Random
from typing import Literal

from sparring.evaluation.conversation import ScenarioRunner, Verdict
from sparring.evaluation.cost import Budget, PriceTable, Usage
from sparring.evaluation.dimensions import ScenarioDraft, draft, sample_dimensions
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.narrative import INSTRUCTIONS, NarrativeModel, brief, uncited_details
from sparring.evaluation.promotion import Decision, Promotion, Rerun
from sparring.evaluation.scenario import Scenario

STORY_ATTEMPTS = 2

Outcome = Literal["pass", "failure", "inconclusive", "discarded"]


def typical_conversation(agent_model: str, simulator_model: str) -> list[Usage]:
    """A generous guess at what one generated Scenario consumes, from its story to the end
    of its conversation. It sets the estimate shown before a run and what each launch
    reserves from the budget."""
    return [
        Usage(agent_model, input_tokens=80_000, output_tokens=5_000),
        Usage(simulator_model, input_tokens=20_000, output_tokens=8_000),
        Usage(simulator_model, input_tokens=1_000 * STORY_ATTEMPTS, output_tokens=1_200),
    ]


@dataclass(frozen=True)
class Explored:
    scenario_id: str
    dimensions: Mapping[str, str]
    outcome: Outcome
    cost: Decimal
    findings: list[str]
    """What went wrong in a Failure, or why the conversation was Inconclusive or its
    Scenario discarded."""
    promotion: Decision | None = None
    """What Promotion decided about a Failure."""

    def costing(self, cost: Decimal) -> "Explored":
        return Explored(
            self.scenario_id, self.dimensions, self.outcome, cost, self.findings, self.promotion
        )


@dataclass(frozen=True)
class ExplorationReport:
    explored: list[Explored]
    not_launched: int
    """Scenarios left out because the budget could not cover them."""
    spent: Decimal
    cap: Decimal

    def count(self, outcome: Outcome) -> int:
        return sum(1 for explored in self.explored if explored.outcome == outcome)

    @property
    def cost_per_resolved_conversation(self) -> Decimal | None:
        """The whole run's cost divided by the conversations that passed (ADR-0020)."""
        resolved = self.count("pass")
        return self.spent / resolved if resolved else None


class Exploration:
    """Generates Scenarios from uniformly sampled Dimensions and runs them concurrently,
    at most `parallelism` at a time, until the count or the budget runs out. Each Failure
    goes to Promotion, whose Reproductions draw on the same budget."""

    def __init__(
        self,
        runner: ScenarioRunner,
        narrator: NarrativeModel,
        promotion: Promotion,
        prices: PriceTable,
        parallelism: int,
    ) -> None:
        self._runner = runner
        self._narrator = narrator
        self._promotion = promotion
        self._prices = prices
        self._parallelism = parallelism

    async def run(self, run_id: str, count: int, budget: Budget, seed: int) -> ExplorationReport:
        random = Random(seed)
        drafts = [
            draft(f"{run_id}-{index:03d}", sample_dimensions(random), random)
            for index in range(count)
        ]
        explored: list[Explored] = []
        slots = asyncio.Semaphore(self._parallelism)
        launched = 0
        async with asyncio.TaskGroup() as group:
            for planned in drafts:
                await slots.acquire()
                if not budget.reserve():
                    slots.release()
                    break
                launched += 1
                group.create_task(self._explore(planned, budget, slots, explored))
        explored.sort(key=lambda e: e.scenario_id)
        return ExplorationReport(explored, count - launched, budget.spent, budget.cap)

    async def _explore(
        self,
        planned: ScenarioDraft,
        budget: Budget,
        slots: asyncio.Semaphore,
        explored: list[Explored],
    ) -> None:
        usage: list[Usage] = []
        reproductions: list[Decimal] = []

        async def reproduce(scenario: Scenario) -> Verdict | None:
            return await self._reproduce(scenario, budget, reproductions)

        try:
            outcome, findings, decision = await self._outcome(planned, usage, reproduce)
        finally:
            cost = self._prices.cost(usage)
            budget.settle(cost)
            slots.release()
        cost += sum(reproductions, Decimal(0))
        explored.append(Explored(planned.id, planned.dimensions, outcome, cost, findings, decision))

    async def _outcome(
        self, planned: ScenarioDraft, usage: list[Usage], reproduce: Rerun
    ) -> tuple[Outcome, list[str], Decision | None]:
        try:
            story, uncited = await self._story(planned, usage)
        except InfrastructureFailure as failure:
            return "inconclusive", [str(failure)], None
        if uncited:
            return "discarded", [f"the story cited {', '.join(uncited)}, not in the facts"], None
        conversation, verdict = await self._runner.run(planned.scenario(story))
        usage += conversation.usage
        if verdict.inconclusive:
            return "inconclusive", [str(conversation.interrupted_by)], None
        if not verdict.failed:
            return "pass", [], None
        decision = await self._promotion.consider(conversation, verdict, reproduce)
        violations = [f"{v.invariant}: {v.detail}" for v in verdict.violations]
        return "failure", [*verdict.differences, *violations], decision

    async def _reproduce(
        self, scenario: Scenario, budget: Budget, costs: list[Decimal]
    ) -> Verdict | None:
        """A Reproduction is a conversation of its own, reserved from the budget like any."""
        if not budget.reserve():
            return None
        usage: list[Usage] = []
        try:
            conversation, verdict = await self._runner.run(scenario)
            usage += conversation.usage
        finally:
            cost = self._prices.cost(usage)
            budget.settle(cost)
            costs.append(cost)
        return verdict

    async def _story(self, planned: ScenarioDraft, usage: list[Usage]) -> tuple[str, list[str]]:
        """A story that cites only the facts, or the last attempt and what it made up."""
        story, uncited = "", []
        for _ in range(STORY_ATTEMPTS):
            written = await self._narrator.write(INSTRUCTIONS, brief(planned))
            usage += [written.usage] if written.usage else []
            story, uncited = written.text, uncited_details(written.text, planned.persona.facts)
            if not uncited:
                break
        return story, uncited
