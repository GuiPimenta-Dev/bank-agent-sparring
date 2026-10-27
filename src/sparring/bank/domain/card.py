import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

from sparring.bank.domain.customer import CustomerId
from sparring.bank.domain.refusals import (
    CardAlreadyFrozen,
    CardCancelled,
    CardNotFrozen,
    FraudFreezeLocked,
)

CardId = NewType("CardId", str)


class FreezeSource(StrEnum):
    CUSTOMER = "customer"
    FRAUD_TEAM = "fraud_team"


class LossReason(StrEnum):
    LOST = "lost"
    STOLEN = "stolen"


@dataclass
class Card:
    id: CardId
    customer_id: CustomerId
    last_four: str
    frozen_by: FreezeSource | None = None
    cancelled_because: LossReason | None = None
    replaces: CardId | None = None

    def freeze(self, by: FreezeSource) -> None:
        self._refuse_if_cancelled()
        if self.frozen_by is not None:
            raise CardAlreadyFrozen(self.id)
        self.frozen_by = by

    def unfreeze(self, by: FreezeSource) -> None:
        self._refuse_if_cancelled()
        if self.frozen_by is None:
            raise CardNotFrozen(self.id)
        if self.frozen_by is FreezeSource.FRAUD_TEAM and by is not FreezeSource.FRAUD_TEAM:
            raise FraudFreezeLocked(self.id)
        self.frozen_by = None

    def cancel(self, because: LossReason) -> "Card":
        """Cancel this card for good and return its Replacement Card (LS-1)."""
        self._refuse_if_cancelled()
        self.cancelled_because = because
        replacement_id = CardId(f"{self.id}-replacement")
        return Card(
            replacement_id, self.customer_id, _last_four_for(replacement_id), replaces=self.id
        )

    def ensure_usable(self) -> None:
        self._refuse_if_cancelled()

    def _refuse_if_cancelled(self) -> None:
        if self.cancelled_because is not None:
            raise CardCancelled(self.id)


def _last_four_for(card_id: CardId) -> str:
    # A new card number, derived from the card id so the Oracle can predict it.
    return f"{int(hashlib.sha256(card_id.encode()).hexdigest(), 16) % 10_000:04d}"
