from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

from sparring.bank.domain.customer import CustomerId
from sparring.bank.domain.refusals import CardAlreadyFrozen, CardNotFrozen, FraudFreezeLocked

CardId = NewType("CardId", str)


class FreezeSource(StrEnum):
    CUSTOMER = "customer"
    FRAUD_TEAM = "fraud_team"


@dataclass
class Card:
    id: CardId
    customer_id: CustomerId
    last_four: str
    frozen_by: FreezeSource | None = None

    def freeze(self, by: FreezeSource) -> None:
        if self.frozen_by is not None:
            raise CardAlreadyFrozen(self.id)
        self.frozen_by = by

    def unfreeze(self, by: FreezeSource) -> None:
        if self.frozen_by is None:
            raise CardNotFrozen(self.id)
        if self.frozen_by is FreezeSource.FRAUD_TEAM and by is not FreezeSource.FRAUD_TEAM:
            raise FraudFreezeLocked(self.id)
        self.frozen_by = None
