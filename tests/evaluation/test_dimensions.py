from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from random import Random

from sparring.bank.domain.card import FreezeSource
from sparring.bank.domain.customer import Tier
from sparring.bank.domain.dispute import DISPUTE_WINDOW, PROVISIONAL_CREDIT_LIMIT
from sparring.bank.domain.transaction import Transaction
from sparring.evaluation.dimensions import (
    NOW,
    OPERATIONS,
    PER_OPERATION,
    SHARED,
    ScenarioDraft,
    draft,
    sample_dimensions,
)
from sparring.evaluation.oracle import Oracle

SEEDS = range(300)


def generated(seed: int) -> ScenarioDraft:
    random = Random(seed)
    return draft(f"generated-{seed}", sample_dimensions(random), random)


def with_dimensions(**dimensions: str) -> ScenarioDraft:
    values = {"tier": "standard", "persona": "calm", "patience": "5", **dimensions}
    return draft("chosen", values, Random(0))


def test_sampling_covers_every_operation_and_every_value() -> None:
    samples = [sample_dimensions(Random(seed)) for seed in SEEDS]

    assert {sample["operation"] for sample in samples} == set(OPERATIONS)
    for operation, dimensions in PER_OPERATION.items():
        for name, values in {**SHARED, **dimensions}.items():
            seen = {s[name] for s in samples if s["operation"] == operation and name in s}
            assert seen == set(values), (operation, name)


def test_sampling_is_reproducible_from_its_seed() -> None:
    assert [sample_dimensions(Random(7)) for _ in range(5)] == [
        sample_dimensions(Random(7)) for _ in range(5)
    ]


def test_every_generated_scenario_is_one_the_oracle_can_judge() -> None:
    for seed in SEEDS:
        planned = generated(seed)
        scenario = planned.scenario("A story.")

        Oracle().expected_outcome(scenario)
        intent = scenario.goal.intent
        assert intent.operation == planned.dimensions["operation"], seed
        assert intent.card_id is None or intent.card_id in {c.id for c in scenario.bank.cards}
        assert set(intent.transaction_ids) <= {t.id for t in scenario.bank.transactions}
        assert scenario.persona.customer_id == scenario.bank.customers[0].id
        assert scenario.goal.narrative.startswith("A story. ")


def test_the_operations_spread_evenly() -> None:
    counts = Counter(sample_dimensions(Random(seed))["operation"] for seed in range(1000))
    assert min(counts.values()) > 150


def test_amount_bands_bracket_the_credit_limit_and_the_escalation_threshold() -> None:
    for tier in ("standard", "premium"):
        limit = PROVISIONAL_CREDIT_LIMIT[Tier(tier)].amount
        for count in ("1", "2", "3"):
            amounts = {
                band: [
                    t.amount.amount
                    for t in dispute(tier=tier, transaction_count=count, amount_band=band)
                ]
                for band in PER_OPERATION["dispute_transaction"]["amount_band"]
            }
            within = amounts["within_provisional_credit_limit"]
            over_limit = amounts["over_provisional_credit_limit"]
            assert max(within) <= limit and sum(within) <= 2000
            assert max(over_limit) > limit and sum(over_limit) <= 2000
            assert sum(amounts["over_escalation_threshold"]) > 2000


def dispute(**dimensions: str) -> list[Transaction]:
    values = {
        "operation": "dispute_transaction",
        "transaction_count": "1",
        "transaction_state": "settled",
        "amount_band": "within_provisional_credit_limit",
        "transaction_age": "recent",
        "recognised": "yes",
        **dimensions,
    }
    planned = with_dimensions(**values)
    ids = set(planned.intent.transaction_ids)
    return [t for t in planned.bank.transactions if t.id in ids]


def test_ages_put_settlement_inside_on_and_past_the_dispute_window() -> None:
    today = NOW.date()
    window = DISPUTE_WINDOW.days
    checks: dict[str, Callable[[int], bool]] = {
        "recent": lambda days: days < window,
        "last_day_of_window": lambda days: days == window,
        "past_window": lambda days: days > window,
    }
    for age, check in checks.items():
        planned = with_dimensions(
            operation="dispute_transaction",
            transaction_count="2",
            transaction_state="settled",
            amount_band="within_provisional_credit_limit",
            transaction_age=age,
            recognised="yes",
        )
        disputed = [t for t in planned.bank.transactions if t.id in planned.intent.transaction_ids]
        for transaction in disputed:
            assert transaction.settled_on is not None
            assert check((today - transaction.settled_on) // timedelta(days=1)), age


def test_only_the_last_of_several_transactions_is_pending_when_some_have_settled() -> None:
    planned = with_dimensions(
        operation="dispute_transaction",
        transaction_count="3",
        transaction_state="settled_and_pending",
        amount_band="within_provisional_credit_limit",
        transaction_age="recent",
        recognised="yes",
    )
    disputed = [t for t in planned.bank.transactions if t.id in planned.intent.transaction_ids]

    assert [t.settled_on is None for t in disputed] == [False, False, True]


def test_card_states_are_set_on_the_card_the_goal_names() -> None:
    fraud = with_dimensions(
        operation="unfreeze_card", card="credit", card_state="frozen_by_fraud_team"
    )
    cancelled = with_dimensions(
        operation="report_lost_or_stolen", card="debit", card_state="cancelled", reason="lost"
    )

    assert fraud.intent.card_id is not None
    assert fraud.bank.card(fraud.intent.card_id).frozen_by is FreezeSource.FRAUD_TEAM
    assert cancelled.intent.card_id is not None
    assert cancelled.bank.card(cancelled.intent.card_id).cancelled_because is not None
    assert any(card.replaces == cancelled.intent.card_id for card in cancelled.bank.cards)


def test_the_customer_s_choices_the_oracle_needs_are_fixed_in_the_narrative() -> None:
    unrecognised = with_dimensions(
        operation="dispute_transaction",
        transaction_count="3",
        transaction_state="settled",
        amount_band="within_provisional_credit_limit",
        transaction_age="recent",
        recognised="no",
    )
    human = with_dimensions(operation="escalate")

    narrative = unrecognised.scenario("").goal.narrative
    assert unrecognised.intent.unrecognised
    assert "If the agent offers to freeze your card, accept." in narrative
    assert "Mention every payment when you first explain the problem." in narrative
    assert "Ask to speak to a human." in human.scenario("").goal.narrative
    assert dict(human.persona.facts) == {"full name": human.persona.name}


def test_the_facts_list_every_disputed_payment_as_the_customer_knows_it() -> None:
    planned = with_dimensions(
        operation="dispute_transaction",
        transaction_count="2",
        transaction_state="settled_and_pending",
        amount_band="within_provisional_credit_limit",
        transaction_age="recent",
        recognised="yes",
    )
    payments = planned.persona.facts["the payments to dispute"]

    for transaction in planned.bank.transactions:
        if transaction.id in planned.intent.transaction_ids:
            assert f"£{transaction.amount.amount:,.2f} to {transaction.merchant}" in payments
    assert payments.endswith("still pending")
    assert Decimal(0) not in [t.amount.amount for t in planned.bank.transactions]
