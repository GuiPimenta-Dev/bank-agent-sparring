from dataclasses import dataclass
from decimal import Decimal
from functools import total_ordering


@total_ordering
@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "GBP"

    def __add__(self, other: "Money") -> "Money":
        return Money(self.amount + self._same_currency(other).amount, self.currency)

    def __le__(self, other: "Money") -> bool:
        return self.amount <= self._same_currency(other).amount

    def __str__(self) -> str:
        symbol = "£" if self.currency == "GBP" else f"{self.currency} "
        return f"{symbol}{self.amount:,.2f}"

    def _same_currency(self, other: "Money") -> "Money":
        if other.currency != self.currency:
            raise ValueError(f"Cannot combine {self.currency} with {other.currency}")
        return other
