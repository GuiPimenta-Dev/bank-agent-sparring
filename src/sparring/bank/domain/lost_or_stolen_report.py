"""The Lost or Stolen Report: what happens once the Customer confirms it (LS-1, LS-3).

Shared by the bank, which executes it, and the Oracle, which predicts it.
"""

from collections.abc import Mapping
from decimal import Decimal

from sparring.bank.domain.card import Card, LossReason
from sparring.bank.domain.customer import Customer, Tier
from sparring.bank.domain.fee import Fee, FeeId
from sparring.bank.domain.money import Money

REPLACEMENT_CARD_FEE: Mapping[Tier, Money | None] = {
    Tier.STANDARD: Money(Decimal("10.00")),
    Tier.PREMIUM: None,
}


def execute_lost_or_stolen_report(
    card: Card, customer: Customer, because: LossReason
) -> tuple[Card, Fee | None]:
    """Cancel the card and return its Replacement Card and the Fee charged for it, if any."""
    replacement = card.cancel(because)
    price = REPLACEMENT_CARD_FEE[customer.tier]
    if price is None:
        return replacement, None
    fee = Fee(
        FeeId(f"fee-{replacement.id}"), customer.id, price, f"Replacement Card {replacement.id}"
    )
    return replacement, fee


def describe_lost_or_stolen_report(card: Card, customer: Customer, because: LossReason) -> str:
    """The summary the Customer confirms in the Customer Channel, rendered by the bank."""
    price = REPLACEMENT_CARD_FEE[customer.tier]
    cost = f"for {price}" if price else "free of charge"
    return (
        f"Report card ending {card.last_four} as {because}: it will be cancelled permanently "
        f"and a Replacement Card with a new number will be issued {cost}."
    )
