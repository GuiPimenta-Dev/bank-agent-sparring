import ast
import sys
from datetime import date
from pathlib import Path

import pytest

import sparring.bank.domain
from sparring.bank.domain.card import Card, CardId, FreezeSource
from sparring.bank.domain.customer import Customer, CustomerId, IdentityClaim
from sparring.bank.domain.refusals import CardAlreadyFrozen, CardNotFrozen, FraudFreezeLocked

ANA = CustomerId("C-1001")


def card(frozen_by: FreezeSource | None = None) -> Card:
    return Card(CardId("card-1"), ANA, "4242", frozen_by)


def test_customer_freeze_is_reversible_by_the_customer() -> None:
    debit = card()
    debit.freeze(FreezeSource.CUSTOMER)
    debit.unfreeze(FreezeSource.CUSTOMER)
    assert debit.frozen_by is None


def test_a_frozen_card_cannot_be_frozen_again() -> None:
    with pytest.raises(CardAlreadyFrozen):
        card(FreezeSource.FRAUD_TEAM).freeze(FreezeSource.CUSTOMER)


def test_only_the_fraud_team_lifts_its_own_freeze() -> None:
    debit = card(FreezeSource.FRAUD_TEAM)
    with pytest.raises(FraudFreezeLocked):
        debit.unfreeze(FreezeSource.CUSTOMER)
    debit.unfreeze(FreezeSource.FRAUD_TEAM)
    assert debit.frozen_by is None


def test_an_unfrozen_card_cannot_be_unfrozen() -> None:
    with pytest.raises(CardNotFrozen):
        card().unfreeze(FreezeSource.CUSTOMER)


def test_identity_claim_must_match_every_detail() -> None:
    ana = Customer(ANA, "Ana Souza", date(1990, 5, 17), "SW1A 1AA")
    assert ana.is_claimed_by(IdentityClaim(ANA, date(1990, 5, 17), "sw1a1aa"))
    assert not ana.is_claimed_by(IdentityClaim(ANA, date(1990, 5, 18), "SW1A 1AA"))
    assert not ana.is_claimed_by(IdentityClaim(ANA, date(1990, 5, 17), "SW1A 2AA"))


def test_bank_domain_imports_only_the_standard_library() -> None:
    domain = Path(sparring.bank.domain.__file__).parent
    for path in domain.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                in_domain = module.startswith("sparring.bank.domain")
                in_stdlib = module.split(".")[0] in sys.stdlib_module_names
                assert in_domain or in_stdlib, f"{path.name} imports {module}"
