import copy
from pathlib import Path

from sparring.bank.domain.card import CardId, FreezeSource
from sparring.evaluation.failure_signature import failure_signature, signature_counts
from sparring.evaluation.golden_set import GoldenItem
from sparring.evaluation.invariants import InvariantViolation
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.scenario import BankSnapshot, Scenario, load_scenario

CASES = Path(__file__).parents[2] / "canonical_cases"
CAP = InvariantViolation("tool_call_cap", "agent turn 2 requested 9 tool calls; the cap is 8")


def signature_of(scenario: Scenario, final: BankSnapshot, *violations: InvariantViolation) -> str:
    differences = Oracle().compare(scenario, final)
    return failure_signature(scenario.goal.intent.operation, differences, violations)


def test_a_signature_names_the_operation_and_how_each_field_went_wrong() -> None:
    scenario = load_scenario(CASES / "freeze-active-debit-card.json")
    final = copy.deepcopy(scenario.bank)
    final.card(CardId("card-ana-credit")).frozen_by = FreezeSource.CUSTOMER

    assert signature_of(scenario, final) == (
        "freeze_card: card.frozen_by customer->none, card.frozen_by none->customer"
    )


def test_scenarios_that_differ_only_in_ids_and_amounts_share_a_signature() -> None:
    ana = load_scenario(CASES / "dispute-wrong-charge-applies-provisional-credit.json")
    ben = load_scenario(CASES / "dispute-at-premium-provisional-credit-limit.json")

    assert (
        signature_of(ana, ana.bank)
        == signature_of(ben, ben.bank)
        == ("dispute_transaction: dispute missing")
    )


def test_a_missing_escalation_has_a_signature_of_its_own() -> None:
    scenario = load_scenario(CASES / "unfreeze-fraud-freeze-stays-frozen.json")

    assert signature_of(scenario, scenario.bank) == "unfreeze_card: escalated true->false"


def test_violated_invariants_join_the_signature_after_the_oracle_s_differences() -> None:
    scenario = load_scenario(CASES / "freeze-active-debit-card.json")
    expected = Oracle().expected_outcome(scenario)

    assert signature_of(scenario, expected, CAP, CAP) == "invariant: tool_call_cap"
    assert signature_of(scenario, scenario.bank, CAP) == (
        "freeze_card: card.frozen_by customer->none | invariant: tool_call_cap"
    )


def test_signatures_are_counted_most_frequent_first_with_their_golden_set_share() -> None:
    scenario = load_scenario(CASES / "freeze-active-debit-card.json")
    golden = [GoldenItem(scenario, "b", None), GoldenItem(scenario, "b", None)]

    counts = signature_counts(["a", "b", "b", "c", "b", "a"], golden)

    assert [(c.signature, c.failures, c.in_golden_set) for c in counts] == [
        ("b", 3, 2),
        ("a", 2, 0),
        ("c", 1, 0),
    ]
