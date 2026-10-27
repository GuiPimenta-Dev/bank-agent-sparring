from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType

from sparring.bank.domain.card import CardId, LossReason
from sparring.bank.domain.customer import CustomerId
from sparring.bank.domain.refusals import PendingActionAlreadyAnswered, PendingActionExpired

PendingActionId = NewType("PendingActionId", str)


class PendingActionStatus(StrEnum):
    AWAITING = "awaiting"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass
class PendingAction:
    """A Lost or Stolen Report the bank holds until the Customer gives a Confirmation."""

    id: PendingActionId
    customer_id: CustomerId
    card_id: CardId
    because: LossReason
    summary: str
    expires_at: datetime
    status: PendingActionStatus = PendingActionStatus.AWAITING

    def is_awaiting(self, now: datetime) -> bool:
        return self.status is PendingActionStatus.AWAITING and now < self.expires_at

    def confirm(self, now: datetime) -> None:
        self._answer(PendingActionStatus.CONFIRMED, now)

    def reject(self, now: datetime) -> None:
        self._answer(PendingActionStatus.REJECTED, now)

    def _answer(self, status: PendingActionStatus, now: datetime) -> None:
        if self.status is not PendingActionStatus.AWAITING:
            raise PendingActionAlreadyAnswered(self.id, self.status)
        if now >= self.expires_at:
            raise PendingActionExpired(self.id)
        self.status = status
