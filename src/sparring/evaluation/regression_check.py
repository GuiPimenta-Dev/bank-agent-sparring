"""Regression Check: the Golden Set played against a proposed agent, each Scenario's Status
compared with the current version's (ADR-0019, ADR-0020).

It stops early: every Scenario runs once, and only a Failure is run up to two more times.
One pass already proves a Scenario is not Failing, the only Status that blocks, so a
Scenario that passed its single run counts as Passing.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol

from langfuse import Langfuse

from sparring.evaluation.conversation import ScenarioRunner, Verdict
from sparring.evaluation.cost import Budget, PriceTable, Usage
from sparring.evaluation.scenario import Scenario

RERUNS = 2
INCONCLUSIVE_LIMIT = 0.2
"""Above this share of Inconclusive conversations, a Regression Check is invalid (ADR-0017)."""


class ScenarioStatus(StrEnum):
    PASSING = "passing"
    FLAKY = "flaky"
    FAILING = "failing"
    INCONCLUSIVE = "inconclusive"
    """No run was conclusive."""
    NOT_RUN = "not_run"
    """The budget ran out first."""


@dataclass(frozen=True)
class CheckedScenario:
    scenario_id: str
    status: ScenarioStatus
    conversations: int
    inconclusive: int
    passed: int
    cost: Decimal


def status_of(verdicts: Sequence[Verdict]) -> ScenarioStatus:
    """Inconclusive runs do not count toward a Scenario's Status."""
    conclusive = [verdict for verdict in verdicts if not verdict.inconclusive]
    if not conclusive:
        return ScenarioStatus.INCONCLUSIVE
    if all(verdict.passed for verdict in conclusive):
        return ScenarioStatus.PASSING
    if any(verdict.passed for verdict in conclusive):
        return ScenarioStatus.FLAKY
    return ScenarioStatus.FAILING


class RegressionCheck:
    """Plays every Scenario concurrently, at most `parallelism` at a time, each run reserved
    from the budget like any conversation."""

    def __init__(self, runner: ScenarioRunner, prices: PriceTable, parallelism: int) -> None:
        self._runner = runner
        self._prices = prices
        self._slots = asyncio.Semaphore(parallelism)

    async def run(self, scenarios: Sequence[Scenario], budget: Budget) -> list[CheckedScenario]:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(self._check(scenario, budget)) for scenario in scenarios]
        return [task.result() for task in tasks]

    async def _check(self, scenario: Scenario, budget: Budget) -> CheckedScenario:
        verdicts: list[Verdict] = []
        cost = Decimal(0)
        async with self._slots:
            for attempt in range(1 + RERUNS):
                if not budget.reserve():
                    return CheckedScenario(
                        scenario.id, ScenarioStatus.NOT_RUN, len(verdicts), 0, 0, cost
                    )
                usage: list[Usage] = []
                try:
                    conversation, verdict = await self._runner.run(scenario)
                    usage += conversation.usage
                finally:
                    spent = self._prices.cost(usage)
                    budget.settle(spent)
                    cost += spent
                verdicts.append(verdict)
                first_run_settled_it = attempt == 0 and not verdict.failed
                if first_run_settled_it or (attempt > 0 and verdict.passed):
                    break
        return CheckedScenario(
            scenario.id,
            status_of(verdicts),
            len(verdicts),
            sum(1 for verdict in verdicts if verdict.inconclusive),
            sum(1 for verdict in verdicts if verdict.passed),
            cost,
        )


@dataclass(frozen=True)
class Baseline:
    """The Scenario Status of every Golden Set Scenario for one version of main."""

    agent_version: str
    simulator_model: str
    statuses: Mapping[str, ScenarioStatus]


Outcome = Literal["pass", "fail", "invalid"]


@dataclass(frozen=True)
class Comparison:
    outcome: Outcome
    regressions: list[str]
    """Scenarios Passing on main and Failing now: they block."""
    warnings: list[str]
    """Scenarios Passing on main and Flaky now."""
    new: list[str]
    """Scenarios the baseline does not know, such as ones promoted since."""
    reasons: list[str]
    """Why the check is invalid, if it is."""

    @property
    def summary(self) -> str:
        if self.outcome == "invalid":
            return f"Invalid: {'; '.join(self.reasons)}"
        if self.outcome == "fail":
            return f"{len(self.regressions)} Regressions: {', '.join(self.regressions)}"
        flaky = f"; {len(self.warnings)} newly Flaky" if self.warnings else ""
        return f"No Regression{flaky}"


def unreliable(checked: Sequence[CheckedScenario]) -> list[str]:
    """Why these Statuses cannot be trusted: the budget ran out before every Scenario ran,
    or too many conversations were Inconclusive (ADR-0017, ADR-0020)."""
    reasons: list[str] = []
    not_run = [c.scenario_id for c in checked if c.status is ScenarioStatus.NOT_RUN]
    if not_run:
        reasons.append(f"the budget ran out before {len(not_run)} Scenarios")
    conversations = sum(c.conversations for c in checked)
    inconclusive = sum(c.inconclusive for c in checked)
    if conversations and inconclusive / conversations > INCONCLUSIVE_LIMIT:
        reasons.append(f"{inconclusive} of {conversations} conversations were Inconclusive")
    return reasons


def compare(baseline: Baseline | None, checked: Sequence[CheckedScenario]) -> Comparison:
    """A Regression is a Scenario Passing on main and Failing now. A check without a
    baseline, or with unreliable Statuses, is invalid: it neither passes nor blocks."""
    reasons = unreliable(checked)
    if baseline is None:
        reasons.insert(0, "there is no baseline for main with this simulator model")
    known: Mapping[str, ScenarioStatus] = baseline.statuses if baseline else {}
    was_passing = {s for s, status in known.items() if status is ScenarioStatus.PASSING}
    regressions = [
        c.scenario_id
        for c in checked
        if c.scenario_id in was_passing and c.status is ScenarioStatus.FAILING
    ]
    warnings = [
        c.scenario_id
        for c in checked
        if c.scenario_id in was_passing and c.status is ScenarioStatus.FLAKY
    ]
    new = [c.scenario_id for c in checked if c.scenario_id not in known]
    outcome: Outcome = "invalid" if reasons else "fail" if regressions else "pass"
    return Comparison(outcome, regressions, warnings, new, reasons)


class RegressionLog(Protocol):
    """Where baselines for main and the results of Regression Checks are kept (ADR-0012)."""

    async def latest_baseline(self, simulator_model: str) -> Baseline | None:
        """The newest baseline recorded with this simulator model; a baseline from another
        simulator model compares nothing (ADR-0006)."""
        ...

    async def record(self, kind: Literal["baseline"] | Outcome, baseline: Baseline) -> None: ...


class InMemoryRegressionLog:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, Baseline]] = []

    async def latest_baseline(self, simulator_model: str) -> Baseline | None:
        baselines = [
            baseline
            for kind, baseline in self.recorded
            if kind == "baseline" and baseline.simulator_model == simulator_model
        ]
        return baselines[-1] if baselines else None

    async def record(self, kind: Literal["baseline"] | Outcome, baseline: Baseline) -> None:
        self.recorded.append((kind, baseline))


class LangfuseRegressionLog:
    """One `regression_check` score per run, on a session of its own: its value says
    whether it is a baseline or a check's outcome, and its metadata holds every Status."""

    SESSION = "regression-checks"

    def __init__(self, langfuse: Langfuse) -> None:
        self._langfuse = langfuse

    async def latest_baseline(self, simulator_model: str) -> Baseline | None:
        return await asyncio.to_thread(self._latest_baseline, simulator_model)

    async def record(self, kind: Literal["baseline"] | Outcome, baseline: Baseline) -> None:
        await asyncio.to_thread(self._record, kind, baseline)

    def _record(self, kind: str, baseline: Baseline) -> None:
        self._langfuse.create_score(
            session_id=self.SESSION,
            name="regression_check",
            value=kind,
            data_type="CATEGORICAL",
            metadata={
                "agent_version": baseline.agent_version,
                "simulator_model": baseline.simulator_model,
                "statuses": {s: str(status) for s, status in baseline.statuses.items()},
            },
        )
        self._langfuse.flush()

    def _latest_baseline(self, simulator_model: str) -> Baseline | None:
        cursor: str | None = None
        while True:
            page = self._langfuse.api.scores.get_many(
                name="regression_check",
                data_type="CATEGORICAL",
                value="baseline",
                fields="core,details",
                cursor=cursor,
            )
            for score in sorted(page.data, key=lambda score: score.timestamp, reverse=True):
                metadata = score.metadata or {}
                if metadata.get("simulator_model") == simulator_model:
                    statuses = metadata["statuses"]
                    return Baseline(
                        metadata["agent_version"],
                        simulator_model,
                        {s: ScenarioStatus(status) for s, status in statuses.items()},
                    )
            cursor = page.meta.cursor
            if not page.data or cursor is None:
                return None
