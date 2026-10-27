import uuid
from collections.abc import Callable
from datetime import timedelta

from sparring.bank.application.ports import Clock, UnitOfWork
from sparring.bank.domain.card import Card, CardId, FreezeSource, LossReason
from sparring.bank.domain.customer import Customer, CustomerId, IdentityClaim
from sparring.bank.domain.lost_or_stolen_report import (
    describe_lost_or_stolen_report,
    execute_lost_or_stolen_report,
)
from sparring.bank.domain.pending_action import PendingAction, PendingActionId
from sparring.bank.domain.refusals import (
    CardNotFound,
    IdentityVerificationFailed,
    PendingActionNotFound,
    VerifiedSessionExpired,
)
from sparring.bank.domain.verified_session import VerifiedSession


class Bank:
    """The bank's card operations, shared by every channel that reaches the bank.

    The agent's operations act on the Customer of an active Verified Session; a card
    or Pending Action of any other Customer does not exist for it. Confirmations come
    only through the Customer Channel, which identifies the Customer itself (ADR-0004).
    """

    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        clock: Clock,
        session_ttl: timedelta = timedelta(minutes=30),
        pending_action_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._session_ttl = session_ttl
        self._pending_action_ttl = pending_action_ttl

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
            card = await self._owned_card(uow, session.customer_id, card_id)
            card.freeze(FreezeSource.CUSTOMER)
            await uow.cards.save(card)
            await uow.commit()
            return card

    async def unfreeze_card(self, session: VerifiedSession, card_id: CardId) -> Card:
        self._require_active(session)
        async with self._unit_of_work() as uow:
            card = await self._owned_card(uow, session.customer_id, card_id)
            card.unfreeze(FreezeSource.CUSTOMER)
            await uow.cards.save(card)
            await uow.commit()
            return card

    async def request_lost_or_stolen_report(
        self, session: VerifiedSession, card_id: CardId, because: LossReason
    ) -> PendingAction:
        """Hold a Lost or Stolen Report until the Customer confirms it (LS-2)."""
        self._require_active(session)
        async with self._unit_of_work() as uow:
            card = await self._owned_card(uow, session.customer_id, card_id)
            card.ensure_usable()
            customer = await self._customer(uow, session.customer_id)
            action = PendingAction(
                PendingActionId(f"pa-{uuid.uuid4().hex[:8]}"),
                customer.id,
                card.id,
                because,
                describe_lost_or_stolen_report(card, customer, because),
                self._clock.now() + self._pending_action_ttl,
            )
            await uow.pending_actions.save(action)
            await uow.commit()
            return action

    async def pending_action(
        self, session: VerifiedSession, pending_action_id: PendingActionId
    ) -> PendingAction:
        self._require_active(session)
        async with self._unit_of_work() as uow:
            return await self._owned_action(uow, session.customer_id, pending_action_id)

    async def awaiting_confirmation(self, customer_id: CustomerId) -> list[PendingAction]:
        """What the Customer Channel shows the Customer right now."""
        now = self._clock.now()
        async with self._unit_of_work() as uow:
            actions = await uow.pending_actions.list_for(customer_id)
        return [action for action in actions if action.is_awaiting(now)]

    async def confirm(self, customer_id: CustomerId, pending_action_id: PendingActionId) -> None:
        """Execute a Pending Action, atomically, on the Customer's Confirmation."""
        async with self._unit_of_work() as uow:
            action = await self._owned_action(uow, customer_id, pending_action_id)
            action.confirm(self._clock.now())
            card = await self._owned_card(uow, customer_id, action.card_id)
            customer = await self._customer(uow, customer_id)
            replacement, fee = execute_lost_or_stolen_report(card, customer, action.because)
            await uow.cards.save(card)
            await uow.cards.save(replacement)
            if fee is not None:
                await uow.fees.add(fee)
            await uow.pending_actions.save(action)
            await uow.commit()

    async def reject(self, customer_id: CustomerId, pending_action_id: PendingActionId) -> None:
        async with self._unit_of_work() as uow:
            action = await self._owned_action(uow, customer_id, pending_action_id)
            action.reject(self._clock.now())
            await uow.pending_actions.save(action)
            await uow.commit()

    def _require_active(self, session: VerifiedSession) -> None:
        if not session.is_active(self._clock.now()):
            raise VerifiedSessionExpired()

    @staticmethod
    async def _owned_card(uow: UnitOfWork, customer_id: CustomerId, card_id: CardId) -> Card:
        card = await uow.cards.get(card_id)
        if card is None or card.customer_id != customer_id:
            raise CardNotFound(card_id)
        return card

    @staticmethod
    async def _owned_action(
        uow: UnitOfWork, customer_id: CustomerId, pending_action_id: PendingActionId
    ) -> PendingAction:
        action = await uow.pending_actions.get(pending_action_id)
        if action is None or action.customer_id != customer_id:
            raise PendingActionNotFound(pending_action_id)
        return action

    @staticmethod
    async def _customer(uow: UnitOfWork, customer_id: CustomerId) -> Customer:
        customer = await uow.customers.get(customer_id)
        if customer is None:
            raise LookupError(f"Customer {customer_id} owns a card but does not exist")
        return customer
