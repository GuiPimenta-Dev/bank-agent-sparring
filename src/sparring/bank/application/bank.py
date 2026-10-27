from collections.abc import Callable
from datetime import timedelta

from sparring.bank.application.ports import Clock, UnitOfWork
from sparring.bank.domain.card import Card, CardId, FreezeSource
from sparring.bank.domain.customer import IdentityClaim
from sparring.bank.domain.refusals import (
    CardNotFound,
    IdentityVerificationFailed,
    VerifiedSessionExpired,
)
from sparring.bank.domain.verified_session import VerifiedSession


class Bank:
    """The bank's card operations, shared by every channel that reaches the bank.

    Every operation except `verify_identity` acts on the Customer of an active
    Verified Session; a card of any other Customer does not exist for it.
    Operations requested through the agent act on the Customer's behalf.
    """

    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        clock: Clock,
        session_ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._session_ttl = session_ttl

    async def verify_identity(self, claim: IdentityClaim) -> VerifiedSession:
        async with self._unit_of_work() as uow:
            customer = await uow.customers.get(claim.customer_id)
        if customer is None or not customer.is_claimed_by(claim):
            raise IdentityVerificationFailed()
        return VerifiedSession(customer.id, self._clock.now() + self._session_ttl)

    async def list_cards(self, session: VerifiedSession) -> list[Card]:
        self._require_active(session)
        async with self._unit_of_work() as uow:
            return await uow.cards.list_for(session.customer_id)

    async def freeze_card(self, session: VerifiedSession, card_id: CardId) -> Card:
        self._require_active(session)
        async with self._unit_of_work() as uow:
            card = await self._owned_card(uow, session, card_id)
            card.freeze(FreezeSource.CUSTOMER)
            await uow.cards.save(card)
            await uow.commit()
            return card

    async def unfreeze_card(self, session: VerifiedSession, card_id: CardId) -> Card:
        self._require_active(session)
        async with self._unit_of_work() as uow:
            card = await self._owned_card(uow, session, card_id)
            card.unfreeze(FreezeSource.CUSTOMER)
            await uow.cards.save(card)
            await uow.commit()
            return card

    def _require_active(self, session: VerifiedSession) -> None:
        if not session.is_active(self._clock.now()):
            raise VerifiedSessionExpired()

    @staticmethod
    async def _owned_card(uow: UnitOfWork, session: VerifiedSession, card_id: CardId) -> Card:
        card = await uow.cards.get(card_id)
        if card is None or card.customer_id != session.customer_id:
            raise CardNotFound(card_id)
        return card
