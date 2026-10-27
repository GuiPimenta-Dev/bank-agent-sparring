from dataclasses import dataclass
from typing import NewType

from sparring.bank.domain.customer import CustomerId
from sparring.bank.domain.money import Money

FeeId = NewType("FeeId", str)


@dataclass(frozen=True)
class Fee:
    id: FeeId
    customer_id: CustomerId
    amount: Money
    reason: str
