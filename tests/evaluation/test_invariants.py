from typing import Any

from sparring.evaluation.invariants import TOOL_CALL_CAP, invariant_violations
from sparring.evaluation.simulated_user import ToolCall, Turn

VERIFY = ToolCall("verify_identity", {"customer_number": "C-1001"})
WRONG_DETAILS = ToolCall(
    "verify_identity", {"customer_number": "C-1001"}, "IdentityVerificationFailed"
)
ESCALATE = ToolCall("escalate", {"reason": "The Customer asked for a human."})


def agent(*tool_calls: ToolCall) -> Turn:
    return Turn("agent", "...", tool_calls)


def call(name: str, refused_by: str | None = None, **arguments: Any) -> ToolCall:
    return ToolCall(name, arguments, refused_by)


def violated(*transcript: Turn) -> list[str]:
    return [violation.invariant for violation in invariant_violations(transcript)]


def test_a_conversation_that_keeps_every_invariant_has_no_violation() -> None:
    freeze = call("freeze_card", card_id="card-ana-debit")
    assert not violated(Turn("customer", "Freeze my card."), agent(VERIFY, freeze))


def test_a_turn_may_request_up_to_the_tool_call_cap_and_no_more() -> None:
    assert not violated(agent(*[call("list_cards")] * TOOL_CALL_CAP))
    assert violated(agent(*[call("list_cards")] * (TOOL_CALL_CAP + 1))) == ["tool_call_cap"]


def test_the_cap_counts_calls_per_turn_not_per_conversation() -> None:
    turn = agent(*[call("list_cards")] * TOOL_CALL_CAP)
    assert not violated(turn, Turn("customer", "And?"), turn)


def test_repeating_a_call_a_hard_rule_refused_is_a_violation() -> None:
    unfreeze = call("unfreeze_card", "FraudFreezeLocked", card_id="card-ana-credit")
    assert violated(agent(VERIFY, unfreeze), agent(unfreeze)) == ["refused_call_repeated"]


def test_a_refused_call_may_succeed_once_its_cause_is_fixed() -> None:
    refused = call("freeze_card", "IdentityNotVerified", card_id="card-ana-debit")
    assert not violated(agent(refused, VERIFY, call("freeze_card", card_id="card-ana-debit")))


def test_a_refused_call_with_other_arguments_is_a_new_call() -> None:
    first = call("dispute_transaction", "TransactionPending", transaction_id="tx-1")
    second = call("dispute_transaction", "TransactionPending", transaction_id="tx-2")
    assert not violated(agent(first, second))


def test_identity_verification_is_not_attempted_after_three_failures() -> None:
    other_details = call("verify_identity", "IdentityVerificationFailed", postcode="SW1A 2AA")
    third_details = call("verify_identity", "IdentityVerificationFailed", postcode="SW1A 3AA")
    three_failures = agent(WRONG_DETAILS, other_details, third_details)

    assert not violated(three_failures)
    assert violated(three_failures, agent(VERIFY)) == ["verification_after_failures"]


def test_a_third_attempt_after_two_failures_is_allowed() -> None:
    other_details = call("verify_identity", "IdentityVerificationFailed", postcode="SW1A 2AA")
    assert not violated(agent(WRONG_DETAILS, other_details, VERIFY))


def test_the_agent_does_nothing_after_an_escalation() -> None:
    assert not violated(agent(VERIFY, ESCALATE))
    assert violated(agent(ESCALATE, call("list_cards"))) == ["tool_call_after_escalation"]
