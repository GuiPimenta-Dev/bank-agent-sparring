from collections.abc import Iterator, Sequence
from decimal import Decimal
from pathlib import Path

import anthropic
import httpx2
import pytest
from fakes import ScriptedModel, calls, says, scripted, unreachable
from langchain_core.messages import AIMessage
from mcp import Client

from sparring.agent.support_agent import SupportAgent
from sparring.evaluation.conversation import ScenarioRunner, Verdict
from sparring.evaluation.cost import Budget, Price, PriceTable
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.recording import InMemoryRecorder
from sparring.evaluation.regression_check import (
    Baseline,
    CheckedScenario,
    InMemoryRegressionLog,
    RegressionCheck,
    ScenarioStatus,
    compare,
    status_of,
)
from sparring.evaluation.scenario import load_scenario
from sparring.evaluation.simulated_user import Turn, UserMove
from sparring.system_under_test import kestrel

PASSING, FLAKY, FAILING = ScenarioStatus.PASSING, ScenarioStatus.FLAKY, ScenarioStatus.FAILING
INCONCLUSIVE, NOT_RUN = ScenarioStatus.INCONCLUSIVE, ScenarioStatus.NOT_RUN
PASSED = Verdict(False, [], [])
FAILED = Verdict(False, ["escalated: expected True, found False"], [], "a signature")
CUT_SHORT = Verdict(True, [], [])
CASE = Path(__file__).parents[2] / "canonical_cases" / "freeze-active-debit-card.json"
ZERO = Decimal(0)


def test_a_status_says_how_reliably_a_scenario_passes() -> None:
    assert status_of([PASSED]) is PASSING
    assert status_of([FAILED, PASSED]) is FLAKY
    assert status_of([FAILED, FAILED, FAILED]) is FAILING
    assert status_of([FAILED, CUT_SHORT, FAILED]) is FAILING, "Inconclusive runs do not count"
    assert status_of([CUT_SHORT]) is INCONCLUSIVE


# Running the Golden Set

VERIFY = calls(
    "verify_identity", customer_number="C-1001", date_of_birth="1990-05-17", postcode="SW1A 1AA"
)


def freezes() -> list[AIMessage]:
    return [VERIFY, calls("freeze_card", card_id="card-ana-debit"), says("Frozen.")]


def does_nothing() -> list[AIMessage]:
    return [says("I am sorry, I cannot help with that.")]


def provider_down() -> ScriptedModel:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return unreachable(anthropic.APIConnectionError(request=request))


class Customer:
    """Asks for a freeze and then leaves, in every conversation."""

    async def next_move(self, instructions: str, transcript: Sequence[Turn]) -> UserMove:
        if any(turn.speaker == "agent" for turn in transcript):
            return UserMove("Bye.", True)
        return UserMove("Freeze my debit card. C-1001, 1990-05-17, SW1A 1AA.", False)


def check(*agents: list[AIMessage] | ScriptedModel) -> RegressionCheck:
    """A check whose agent behaves, conversation after conversation, as `agents` say."""
    behaviours: Iterator[list[AIMessage] | ScriptedModel] = iter(agents)

    async def connect_agent(client: Client) -> SupportAgent:
        behaviour = next(behaviours)
        model = scripted(*behaviour) if isinstance(behaviour, list) else behaviour
        return await SupportAgent.with_model(client, model)

    runner = ScenarioRunner(
        kestrel(connect_agent, "scripted"), Customer(), Oracle(), InMemoryRecorder()
    )
    return RegressionCheck(runner, PriceTable({"scripted": Price(ZERO, ZERO, ZERO, ZERO)}), 2)


def budget(cap: str = "10") -> Budget:
    return Budget(Decimal(cap), Decimal("1"))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("agents", "status", "conversations"),
    [
        ([freezes()], PASSING, 1),
        ([does_nothing(), freezes()], FLAKY, 2),
        ([does_nothing(), does_nothing(), does_nothing()], FAILING, 3),
        ([provider_down()], INCONCLUSIVE, 1),
    ],
    ids=["passing", "flaky", "failing", "inconclusive"],
)
async def test_each_scenario_runs_once_and_only_failures_run_again(
    agents: list[list[AIMessage] | ScriptedModel], status: ScenarioStatus, conversations: int
) -> None:
    [checked] = await check(*agents).run([load_scenario(CASE)], budget())

    assert (checked.status, checked.conversations) == (status, conversations)


@pytest.mark.anyio
async def test_a_scenario_the_budget_cannot_cover_is_not_run() -> None:
    [first, second] = await check(freezes(), freezes()).run(
        [load_scenario(CASE), load_scenario(CASE)], budget("1")
    )

    assert {first.status, second.status} == {PASSING, NOT_RUN}


# Comparing with main


def checked(scenario_id: str, status: ScenarioStatus, inconclusive: int = 0) -> CheckedScenario:
    runs = 1 if status in (PASSING, INCONCLUSIVE) else 3
    return CheckedScenario(scenario_id, status, runs, inconclusive, 0, ZERO)


def main_with(**statuses: ScenarioStatus) -> Baseline:
    return Baseline("abc1234", "pinned-simulator", statuses)


def test_a_regression_fails_the_check_and_names_its_scenarios() -> None:
    comparison = compare(
        main_with(a=PASSING, b=PASSING, c=FLAKY),
        [checked("a", FAILING), checked("b", PASSING), checked("c", FAILING)],
    )

    assert comparison.outcome == "fail"
    assert comparison.regressions == ["a"]
    assert comparison.summary == "1 Regressions: a"


def test_passing_to_flaky_is_a_warning_not_a_failure() -> None:
    comparison = compare(main_with(a=PASSING), [checked("a", FLAKY)])

    assert comparison.outcome == "pass" and comparison.warnings == ["a"]


def test_a_scenario_promoted_since_the_baseline_is_new_and_never_blocks() -> None:
    comparison = compare(main_with(a=PASSING), [checked("a", PASSING), checked("b", FAILING)])

    assert comparison.outcome == "pass" and comparison.new == ["b"]


@pytest.mark.parametrize(
    ("baseline", "scenarios", "reason"),
    [
        (None, [checked("a", PASSING)], "no baseline"),
        (main_with(a=PASSING), [checked("a", NOT_RUN)], "budget ran out"),
        (
            main_with(a=PASSING, b=PASSING),
            [checked("a", INCONCLUSIVE, inconclusive=1), checked("b", PASSING)],
            "Inconclusive",
        ),
    ],
    ids=["no-baseline", "budget-exhausted", "too-many-inconclusive"],
)
def test_a_check_that_cannot_be_trusted_is_invalid_never_a_pass(
    baseline: Baseline | None, scenarios: list[CheckedScenario], reason: str
) -> None:
    comparison = compare(baseline, scenarios)

    assert comparison.outcome == "invalid"
    assert reason in comparison.summary


def test_an_invalid_check_is_invalid_even_with_a_regression() -> None:
    comparison = compare(
        main_with(a=PASSING, b=PASSING), [checked("a", FAILING), checked("b", NOT_RUN)]
    )

    assert comparison.outcome == "invalid"


@pytest.mark.anyio
async def test_only_a_baseline_from_the_same_simulator_model_compares() -> None:
    log = InMemoryRegressionLog()
    await log.record("baseline", Baseline("old", "pinned-simulator", {"a": PASSING}))
    await log.record("baseline", Baseline("new", "pinned-simulator", {"a": FLAKY}))
    await log.record("pass", Baseline("pr", "pinned-simulator", {"a": FAILING}))

    latest = await log.latest_baseline("pinned-simulator")

    assert latest is not None and latest.agent_version == "new"
    assert await log.latest_baseline("another-simulator") is None
