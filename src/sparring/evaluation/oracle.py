import copy
from dataclasses import dataclass

from sparring.bank.domain.card import FreezeSource
from sparring.bank.domain.lost_or_stolen_report import execute_lost_or_stolen_report
from sparring.bank.domain.refusals import HardRuleRefusal
from sparring.evaluation.scenario import BankSnapshot, Scenario


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
        intent = scenario.goal.intent
        card = expected.card(intent.card_id)
        try:
            match intent.operation:
                case "freeze_card":
                    card.freeze(FreezeSource.CUSTOMER)
                case "unfreeze_card":
                    card.unfreeze(FreezeSource.CUSTOMER)
                case "report_lost_or_stolen":
                    assert intent.reason is not None, "validated when the Scenario is loaded"
                    customer = expected.customer(card.customer_id)
                    replacement, fee = execute_lost_or_stolen_report(card, customer, intent.reason)
                    expected.cards.append(replacement)
                    expected.fees.extend([fee] if fee else [])
        except HardRuleRefusal:
            return copy.deepcopy(scenario.bank)
        return expected

    def judge(self, scenario: Scenario, final: BankSnapshot) -> Verdict:
        differences = self.expected_outcome(scenario).differences(final)
        return Verdict(passed=not differences, differences=differences)
