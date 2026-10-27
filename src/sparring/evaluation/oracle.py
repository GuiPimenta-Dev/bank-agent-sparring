import copy
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sparring.bank.domain.card import FreezeSource, LossReason
from sparring.bank.domain.dispute import Dispute, ensure_disputable, open_dispute
from sparring.bank.domain.lost_or_stolen_report import execute_lost_or_stolen_report
from sparring.bank.domain.money import Money
from sparring.bank.domain.refusals import FraudFreezeLocked, HardRuleRefusal
from sparring.bank.domain.transaction import Transaction, TransactionId
from sparring.evaluation.scenario import BankSnapshot, Intent, Scenario

ESCALATION_THRESHOLD = Money(Decimal("2000.00"))
UNRECOGNISED_TRANSACTIONS_TO_ESCALATE = 3


@dataclass(frozen=True)
class Verdict:
    passed: bool
    differences: list[str]


class Oracle:
    """The deterministic Policy: the only authority on what correct means (ADR-0001).

    Hard Rules and the mechanics of each operation are reused from the bank's domain;
    Judgment Rules are implemented here, independently of the agent (ADR-0003). A
    refused operation leaves the bank unchanged; of several transactions to dispute, only
    the refused ones are left out.
    """

    def expected_outcome(self, scenario: Scenario) -> BankSnapshot:
        expected = copy.deepcopy(scenario.bank)
        try:
            match scenario.goal.intent:
                case Intent("freeze_card", str() as card_id):
                    expected.card(card_id).freeze(FreezeSource.CUSTOMER)
                case Intent("unfreeze_card", str() as card_id):
                    try:
                        expected.card(card_id).unfreeze(FreezeSource.CUSTOMER)
                    except FraudFreezeLocked:
                        expected.escalated = True  # FR-4
                case Intent("report_lost_or_stolen", str() as card_id, LossReason() as reason):
                    card = expected.card(card_id)
                    customer = expected.customer(card.customer_id)
                    replacement, fee = execute_lost_or_stolen_report(card, customer, reason)
                    expected.cards.append(replacement)
                    expected.fees.extend([fee] if fee else [])
                case Intent("dispute_transaction", transaction_ids=ids, unrecognised=unrecognised):
                    _dispute(expected, ids, unrecognised, scenario.now)
                case Intent("escalate"):
                    expected.escalated = True  # ES-2
                case intent:
                    raise ValueError(
                        f"Scenario {scenario.id}: an Intent the loader rejects: {intent}"
                    )
        except HardRuleRefusal:
            return copy.deepcopy(scenario.bank)
        return expected

    def judge(self, scenario: Scenario, final: BankSnapshot) -> Verdict:
        differences = self.expected_outcome(scenario).differences(final)
        return Verdict(passed=not differences, differences=differences)


def _dispute(
    expected: BankSnapshot,
    transaction_ids: Sequence[TransactionId],
    unrecognised: bool,
    now: datetime,
) -> None:
    transactions = [expected.transaction(t) for t in transaction_ids]
    if unrecognised:  # DS-7
        for card_id in dict.fromkeys(t.card_id for t in transactions):
            card = expected.card(card_id)
            if card.frozen_by is None and card.cancelled_because is None:
                card.freeze(FreezeSource.CUSTOMER)
    disputable = [t for t in transactions if _disputable(t, expected.disputes, now)]
    total = sum((t.amount for t in disputable), Money(Decimal(0)))
    many_unrecognised = unrecognised and len(transactions) >= UNRECOGNISED_TRANSACTIONS_TO_ESCALATE
    if total > ESCALATION_THRESHOLD or many_unrecognised:  # ES-3, ES-4
        expected.escalated = True
        return
    for transaction in disputable:
        customer = expected.customer(transaction.customer_id)
        expected.disputes.append(open_dispute(transaction, customer, expected.disputes, now))


def _disputable(transaction: Transaction, disputes: Sequence[Dispute], now: datetime) -> bool:
    try:
        ensure_disputable(transaction, disputes, now)
    except HardRuleRefusal:
        return False
    return True
