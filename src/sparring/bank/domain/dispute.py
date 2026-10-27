"""The Dispute: which transactions can be disputed (DS-1 to DS-3) and the Provisional
Credit an opened Dispute brings (DS-5).

Shared by the bank, which opens Disputes, and the Oracle, which predicts them.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import NewType
from zoneinfo import ZoneInfo

from sparring.bank.domain.customer import Customer, CustomerId, Tier
from sparring.bank.domain.money import Money
from sparring.bank.domain.refusals import (
    DisputeAlreadyOpen,
    DisputeWindowExpired,
    TransactionPending,
)
from sparring.bank.domain.transaction import Transaction, TransactionId

DisputeId = NewType("DisputeId", str)

DISPUTE_WINDOW = timedelta(days=60)
PROVISIONAL_CREDIT_LIMIT: Mapping[Tier, Money] = {
    Tier.STANDARD: Money(Decimal("500.00")),
    Tier.PREMIUM: Money(Decimal("1000.00")),
}
# Kestrel Bank's days are London days, so the window closes at London midnight.
BANK_TIME_ZONE = ZoneInfo("Europe/London")


@dataclass(frozen=True)
class Dispute:
    id: DisputeId
    customer_id: CustomerId
    transaction_id: TransactionId
    provisional_credit: Money | None


def ensure_disputable(transaction: Transaction, disputes: Iterable[Dispute], now: datetime) -> None:
    """Refuse a transaction that is pending, past the window, or already disputed."""
    if transaction.settled_on is None:
        raise TransactionPending(transaction.id)
    if now.astimezone(BANK_TIME_ZONE).date() > transaction.settled_on + DISPUTE_WINDOW:
        raise DisputeWindowExpired(transaction.id, DISPUTE_WINDOW.days)
    if any(dispute.transaction_id == transaction.id for dispute in disputes):
        raise DisputeAlreadyOpen(transaction.id)


def open_dispute(
    transaction: Transaction, customer: Customer, disputes: Iterable[Dispute], now: datetime
) -> Dispute:
    """Open a Dispute on the transaction, with its Provisional Credit if the amount allows."""
    ensure_disputable(transaction, disputes, now)
    return Dispute(
        DisputeId(f"dispute-{transaction.id}"),
        customer.id,
        transaction.id,
        provisional_credit_for(transaction, customer),
    )


def provisional_credit_for(transaction: Transaction, customer: Customer) -> Money | None:
    """The full amount, if it is within the Customer's Tier limit; otherwise none."""
    within_limit = transaction.amount <= PROVISIONAL_CREDIT_LIMIT[customer.tier]
    return transaction.amount if within_limit else None


def describe_dispute(transaction: Transaction, customer: Customer) -> str:
    """The summary the Customer confirms in the Customer Channel, rendered by the bank."""
    credit = provisional_credit_for(transaction, customer)
    outcome = (
        f"Provisional Credit of {credit} will be applied while it is investigated"
        if credit
        else "no Provisional Credit applies, because the amount is above "
        f"{PROVISIONAL_CREDIT_LIMIT[customer.tier]}"
    )
    return (
        f"Dispute the {transaction.amount} transaction at {transaction.merchant} "
        f"settled on {transaction.settled_on}: {outcome}."
    )
