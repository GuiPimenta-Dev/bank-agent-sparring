"""The demo Customer and her bank, shared by the terminal chat and the bank service."""

from datetime import date, timedelta
from decimal import Decimal

from sparring.bank.domain.card import Card, CardId, FreezeSource
from sparring.bank.domain.customer import Customer, CustomerId, Tier
from sparring.bank.domain.money import Money
from sparring.bank.domain.transaction import Transaction, TransactionId

ANA = Customer(CustomerId("C-1001"), "Ana Souza", date(1990, 5, 17), "SW1A 1AA", Tier.STANDARD)
ANA_DEBIT = CardId("card-ana-debit")
ANA_CARDS = [
    Card(ANA_DEBIT, ANA.id, "4242"),
    Card(CardId("card-ana-credit"), ANA.id, "0005", FreezeSource.FRAUD_TEAM),
]


def ana_transactions(today: date) -> list[Transaction]:
    """Settled recently, still pending, over the Provisional Credit limit, and too old."""
    rows = [
        ("tx-ana-1", "Pret A Manger", "8.45", 2),
        ("tx-ana-2", "Ticketline", "64.00", None),
        ("tx-ana-3", "Currys", "749.00", 10),
        ("tx-ana-4", "Ryanair", "212.30", 75),
    ]
    return [
        Transaction(
            TransactionId(transaction_id),
            ANA.id,
            ANA_DEBIT,
            merchant,
            Money(Decimal(amount)),
            today - timedelta(days=age) if age is not None else None,
        )
        for transaction_id, merchant, amount, age in rows
    ]


INTRODUCTION = (
    f"You are {ANA.full_name}: customer number {ANA.id}, born {ANA.date_of_birth:%Y-%m-%d}, "
    f"postcode {ANA.postcode}. Your credit card was frozen by the fraud team, and your debit "
    "card has four recent transactions you could dispute."
)
