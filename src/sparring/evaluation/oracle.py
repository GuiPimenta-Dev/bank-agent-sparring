import copy
from dataclasses import dataclass

from sparring.bank.domain.card import FreezeSource, LossReason
from sparring.bank.domain.dispute import open_dispute
from sparring.bank.domain.lost_or_stolen_report import execute_lost_or_stolen_report
from sparring.bank.domain.refusals import HardRuleRefusal
from sparring.evaluation.scenario import BankSnapshot, Intent, Scenario


@dataclass(frozen=True)
class Verdict:
    passed: bool
    differences: list[str]


class Oracle:
    """The deterministic Policy: the only authority on what correct means (ADR-0001).

    Hard Rules and the mechanics of each operation are reused from the bank's domain
    (ADR-0003); a refused operation means the correct outcome leaves the bank unchanged.
    """

    def expected_outcome(self, scenario: Scenario) -> BankSnapshot:
        expected = copy.deepcopy(scenario.bank)
        try:
            match scenario.goal.intent:
                case Intent("freeze_card", str() as card_id):
                    expected.card(card_id).freeze(FreezeSource.CUSTOMER)
                case Intent("unfreeze_card", str() as card_id):
                    expected.card(card_id).unfreeze(FreezeSource.CUSTOMER)
                case Intent("report_lost_or_stolen", str() as card_id, LossReason() as reason):
                    card = expected.card(card_id)
                    customer = expected.customer(card.customer_id)
                    replacement, fee = execute_lost_or_stolen_report(card, customer, reason)
                    expected.cards.append(replacement)
                    expected.fees.extend([fee] if fee else [])
                case Intent("dispute_transaction", transaction_id=str() as transaction_id):
                    transaction = expected.transaction(transaction_id)
                    customer = expected.customer(transaction.customer_id)
                    dispute = open_dispute(transaction, customer, expected.disputes, scenario.now)
                    expected.disputes.append(dispute)
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
