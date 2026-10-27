from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "GBP"

    def __str__(self) -> str:
        symbol = "£" if self.currency == "GBP" else f"{self.currency} "
        return f"{symbol}{self.amount:,.2f}"
