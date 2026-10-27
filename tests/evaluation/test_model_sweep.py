from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest
from fakes import calls, says, scripted
from langchain_core.messages import AIMessage
from mcp import Client

from sparring.agent.support_agent import SupportAgent
from sparring.evaluation.conversation import ScenarioRunner, Verdict
from sparring.evaluation.cost import Budget, Price, PriceTable, Usage
from sparring.evaluation.model_sweep import (
    Cell,
    CellResult,
    InMemoryExperiments,
    ModelSweep,
    Played,
)
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.recording import InMemoryRecorder
from sparring.evaluation.scenario import load_scenario
from sparring.evaluation.simulated_user import Turn, UserMove
from sparring.system_under_test import kestrel

CASE = Path(__file__).parents[2] / "canonical_cases" / "freeze-active-debit-card.json"
STRONG, WEAK = Cell("strong-model", "high"), Cell("weak-model", None)
PRICES = PriceTable(
    {
        "strong-model": Price(Decimal(10), Decimal(10), Decimal(1), Decimal(10)),
        "weak-model": Price(Decimal(1), Decimal(1), Decimal(0), Decimal(1)),
    }
)
PASSED = Verdict(False, [], [])
FAILED = Verdict(False, ["..."], [], "freeze_card: card.frozen_by customer->none")


def test_a_cell_reports_its_resolved_rate_cost_and_failure_signatures() -> None:
    result = CellResult(
        STRONG,
        [
            Played("a", PASSED, Decimal("0.03")),
            Played("b", FAILED, Decimal("0.02")),
            Played("c", Verdict(True, [], []), Decimal("0.01")),
            Played("d", None, Decimal(0)),
        ],
    )

    assert (result.resolved, result.conclusive, result.not_run) == (1, 2, 1)
    assert result.resolved_rate == 0.5
    assert result.cost_per_resolved == Decimal("0.06")
    assert result.signatures == {"freeze_card: card.frozen_by customer->none": 1}


def test_a_cell_with_nothing_resolved_has_no_cost_per_resolved() -> None:
    assert CellResult(WEAK, [Played("a", FAILED, Decimal(1))]).cost_per_resolved is None


VERIFY = calls(
    "verify_identity", customer_number="C-1001", date_of_birth="1990-05-17", postcode="SW1A 1AA"
)


class Customer:
    async def next_move(self, instructions: str, transcript: Sequence[Turn]) -> UserMove:
        if any(turn.speaker == "agent" for turn in transcript):
            return UserMove("Bye.", True)
        return UserMove("Freeze my debit card. C-1001, 1990-05-17, SW1A 1AA.", False)


def runner_for(cell: Cell) -> ScenarioRunner:
    """The strong model freezes the card; the weak one gives up."""

    async def connect_agent(client: Client) -> SupportAgent:
        script: list[AIMessage] = (
            [VERIFY, calls("freeze_card", card_id="card-ana-debit"), says("Frozen.")]
            if cell is STRONG
            else [says("Sorry, I can't do that.")]
        )
        script[-1].usage_metadata = {
            "input_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
        }
        return await SupportAgent.with_model(client, scripted(*script))

    return ScenarioRunner(
        kestrel(connect_agent, cell.model), Customer(), Oracle(), InMemoryRecorder()
    )


@pytest.mark.anyio
async def test_each_cell_plays_the_golden_set_as_a_run_of_its_own() -> None:
    experiments = InMemoryExperiments([load_scenario(CASE)] * 3, parallelism=2)
    sweep = ModelSweep(runner_for, experiments, PRICES)

    strong, weak = await sweep.run(
        "sweep abc1234", [STRONG, WEAK], Budget(Decimal(1), Decimal("0.05")), {}
    )

    assert experiments.runs == [
        "sweep abc1234 strong-model (effort high)",
        "sweep abc1234 weak-model (effort default)",
    ]
    assert (strong.resolved, strong.resolved_rate) == (3, 1.0)
    assert (weak.resolved, weak.resolved_rate) == (0, 0.0)
    assert strong.cost == 3 * Decimal("0.011") and weak.cost == 3 * Decimal("0.0011")
    assert weak.signatures == {"freeze_card: card.frozen_by customer->none": 3}


@pytest.mark.anyio
async def test_the_cap_leaves_cells_unfinished_rather_than_overspend() -> None:
    sweep = ModelSweep(runner_for, InMemoryExperiments([load_scenario(CASE)] * 3, 1), PRICES)

    [strong, weak] = await sweep.run(
        "sweep", [STRONG, WEAK], Budget(Decimal("0.03"), Decimal("0.012")), {}
    )

    assert strong.not_run == 1 and weak.not_run == 3
    assert strong.cost + weak.cost <= Decimal("0.03")


def test_usage_is_priced_as_the_cell_s_model() -> None:
    assert PRICES.cost([Usage("strong-model", input_tokens=1000, output_tokens=100)]) == Decimal(
        "0.011"
    )
