from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "GBP"

    def __le__(self, other: "Money") -> bool:
        if other.currency != self.currency:
            raise ValueError(f"Cannot compare {self.currency} with {other.currency}")
        return self.amount <= other.amount

    def __str__(self) -> str:
        symbol = "£" if self.currency == "GBP" else f"{self.currency} "
        return f"{symbol}{self.amount:,.2f}"
