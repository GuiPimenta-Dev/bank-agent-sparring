from decimal import Decimal

import pytest

from sparring.evaluation.cost import Budget, Price, PriceTable, UnpricedModel, Usage

TABLE = PriceTable({"agent": Price(Decimal("1"), Decimal("5"), Decimal("0.1"), Decimal("1.25"))})


def test_each_kind_of_token_is_priced_per_million() -> None:
    usage = Usage(
        "agent",
        input_tokens=1_000_000,
        output_tokens=200_000,
        cache_read_tokens=500_000,
        cache_write_tokens=100_000,
    )
    assert TABLE.cost([usage, usage]) == 2 * (
        Decimal("1") + Decimal("1") + Decimal("0.05") + Decimal("0.125")
    )


def test_a_model_without_a_price_is_refused_before_anything_runs() -> None:
    with pytest.raises(UnpricedModel, match="unknown-model"):
        TABLE.require("agent", "unknown-model")
    with pytest.raises(UnpricedModel):
        TABLE.cost([Usage("unknown-model")])


def test_the_versioned_table_prices_the_default_agent_model() -> None:
    PriceTable.load().require("claude-haiku-5-5")


def test_the_budget_reserves_an_estimate_for_every_conversation_still_running() -> None:
    budget = Budget(cap=Decimal("0.30"), estimate=Decimal("0.10"))

    assert budget.reserve() and budget.reserve() and budget.reserve()
    assert not budget.reserve()
    budget.settle(Decimal("0.02"))
    assert not budget.reserve(), "0.02 spent + 2 running + 1 more is over the cap"
    budget.settle(Decimal("0.02"))
    budget.settle(Decimal("0.02"))
    assert budget.spent == Decimal("0.06")
    assert budget.reserve() and budget.reserve()
    assert not budget.reserve()
