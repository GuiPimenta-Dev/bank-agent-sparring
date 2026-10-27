"""Model sweep: the Golden Set over a grid of agent models and effort levels, to choose the
agent's production model by cost per resolved conversation (ADR-0006, ADR-0020).

Each cell plays every Golden Set Scenario once and is recorded as a Langfuse experiment
run on the Golden Set Dataset, so cells can be compared side by side (ADR-0005).
"""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from langfuse import Evaluation, Langfuse

from sparring.evaluation.conversation import ScenarioRunner, Verdict
from sparring.evaluation.cost import Budget, PriceTable, Usage
from sparring.evaluation.scenario import Scenario, scenario_from_json


@dataclass(frozen=True)
class Cell:
    model: str
    effort: str | None
    """None leaves the model at its default effort."""

    @property
    def label(self) -> str:
        return f"{self.model} (effort {self.effort or 'default'})"


@dataclass(frozen=True)
class Played:
    """One Golden Set Scenario played in one cell."""

    scenario_id: str
    verdict: Verdict | None
    """None when the budget could not cover the conversation."""
    cost: Decimal


@dataclass(frozen=True)
class CellResult:
    cell: Cell
    played: list[Played]

    @property
    def resolved(self) -> int:
        return sum(1 for p in self.played if p.verdict and p.verdict.passed)

    @property
    def conclusive(self) -> int:
        return sum(1 for p in self.played if p.verdict and not p.verdict.inconclusive)

    @property
    def not_run(self) -> int:
        return sum(1 for p in self.played if p.verdict is None)

    @property
    def cost(self) -> Decimal:
        return sum((p.cost for p in self.played), Decimal(0))

    @property
    def resolved_rate(self) -> float | None:
        """Resolved conversations among the conclusive ones."""
        return self.resolved / self.conclusive if self.conclusive else None

    @property
    def cost_per_resolved(self) -> Decimal | None:
        return self.cost / self.resolved if self.resolved else None

    @property
    def signatures(self) -> Counter[str]:
        return Counter(
            p.verdict.signature for p in self.played if p.verdict and p.verdict.signature
        )


Task = Callable[[Scenario], Awaitable[Played]]


class Experiments(Protocol):
    """Runs a task over every Golden Set Scenario as one comparable run."""

    async def run(self, run_name: str, metadata: Mapping[str, str], task: Task) -> list[Played]: ...


class InMemoryExperiments:
    def __init__(self, scenarios: Sequence[Scenario], parallelism: int) -> None:
        self._scenarios = scenarios
        self._parallelism = parallelism
        self.runs: list[str] = []

    async def run(self, run_name: str, metadata: Mapping[str, str], task: Task) -> list[Played]:
        self.runs.append(run_name)
        slots = asyncio.Semaphore(self._parallelism)

        async def limited(scenario: Scenario) -> Played:
            async with slots:
                return await task(scenario)

        return list(await asyncio.gather(*(limited(s) for s in self._scenarios)))


class LangfuseExperiments:
    """A Langfuse experiment run over the Golden Set Dataset, with a `resolved` score per
    item. The SDK runs each experiment on an event loop of its own, in a worker thread, so
    everything a task uses must be created inside the task."""

    def __init__(self, langfuse: Langfuse, parallelism: int, dataset: str = "golden-set") -> None:
        self._langfuse = langfuse
        self._parallelism = parallelism
        self._dataset = dataset

    async def run(self, run_name: str, metadata: Mapping[str, str], task: Task) -> list[Played]:
        dataset = await asyncio.to_thread(self._langfuse.get_dataset, self._dataset)
        played: list[Played] = []

        async def item_task(*, item: Any, **_: Any) -> dict[str, Any]:
            result = await task(scenario_from_json(item.input))
            played.append(result)
            verdict = result.verdict
            return {
                "scenario_id": result.scenario_id,
                "resolved": bool(verdict and verdict.passed),
                "failure_signature": verdict.signature if verdict else None,
                "cost_usd": str(result.cost),
            }

        def resolved(*, output: dict[str, Any], **_: Any) -> Evaluation:
            return Evaluation(name="resolved", value=output["resolved"], data_type="BOOLEAN")

        await asyncio.to_thread(
            dataset.run_experiment,
            name="model sweep",
            run_name=run_name,
            task=item_task,
            evaluators=[resolved],
            max_concurrency=self._parallelism,
            metadata=dict(metadata),
        )
        self._langfuse.flush()
        return played


class ModelSweep:
    """Plays the Golden Set once per cell, one cell after another, every conversation
    reserved from one budget."""

    def __init__(
        self,
        runner_for: Callable[[Cell], ScenarioRunner],
        experiments: Experiments,
        prices: PriceTable,
    ) -> None:
        self._runner_for = runner_for
        self._experiments = experiments
        self._prices = prices

    async def run(
        self, run_prefix: str, cells: Sequence[Cell], budget: Budget, metadata: Mapping[str, str]
    ) -> list[CellResult]:
        results: list[CellResult] = []
        for cell in cells:
            cell_metadata = {
                **metadata,
                "agent_model": cell.model,
                "agent_effort": cell.effort or "default",
            }
            played = await self._experiments.run(
                f"{run_prefix} {cell.label}", cell_metadata, self._task(cell, budget)
            )
            results.append(CellResult(cell, sorted(played, key=lambda p: p.scenario_id)))
        return results

    def _task(self, cell: Cell, budget: Budget) -> Task:
        """The cell's runner is made on first use, on the event loop its experiment runs on."""
        runners: list[ScenarioRunner] = []

        async def play(scenario: Scenario) -> Played:
            if not runners:
                runners.append(self._runner_for(cell))
            return await self._play(runners[0], scenario, budget)

        return play

    async def _play(self, runner: ScenarioRunner, scenario: Scenario, budget: Budget) -> Played:
        if not budget.reserve():
            return Played(scenario.id, None, Decimal(0))
        usage: list[Usage] = []
        try:
            conversation, verdict = await runner.run(scenario)
            usage += conversation.usage
        finally:
            cost = self._prices.cost(usage)
            budget.settle(cost)
        return Played(scenario.id, verdict, cost)
