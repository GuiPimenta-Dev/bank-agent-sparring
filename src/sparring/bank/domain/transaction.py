from dataclasses import dataclass
from datetime import date
from typing import NewType

from sparring.bank.domain.card import CardId
from sparring.bank.domain.customer import CustomerId
from sparring.bank.domain.money import Money

TransactionId = NewType("TransactionId", str)


@dataclass(frozen=True)
class Transaction:
    """Money moved with one of the Customer's cards: pending until it settles, on `settled_on`."""

    id: TransactionId
    customer_id: CustomerId
    card_id: CardId
    merchant: str
    amount: Money
    settled_on: date | None = None
