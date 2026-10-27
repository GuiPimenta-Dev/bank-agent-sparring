import asyncio
import copy
from collections.abc import Iterable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

from sparring.bank.domain.card import Card, CardId
from sparring.bank.domain.customer import Customer, CustomerId
from sparring.bank.domain.dispute import Dispute, DisputeId
from sparring.bank.domain.escalation import Escalation, EscalationId
from sparring.bank.domain.fee import Fee, FeeId
from sparring.bank.domain.pending_action import PendingAction, PendingActionId
from sparring.bank.domain.transaction import Transaction, TransactionId


@dataclass
class BankState:
    customers: dict[CustomerId, Customer] = field(default_factory=dict[CustomerId, Customer])
    cards: dict[CardId, Card] = field(default_factory=dict[CardId, Card])
    pending_actions: dict[PendingActionId, PendingAction] = field(
        default_factory=dict[PendingActionId, PendingAction]
    )
    fees: dict[FeeId, Fee] = field(default_factory=dict[FeeId, Fee])
    transactions: dict[TransactionId, Transaction] = field(
        default_factory=dict[TransactionId, Transaction]
    )
    disputes: dict[DisputeId, Dispute] = field(default_factory=dict[DisputeId, Dispute])
    escalations: dict[EscalationId, Escalation] = field(
        default_factory=dict[EscalationId, Escalation]
    )


class InMemoryBank:
    """Bank state held in memory; each simulated conversation gets its own (ADR-0009).

    Serving a single conversation, one lock serializing every Unit of Work is enough.
    """

    def __init__(
        self,
        customers: Iterable[Customer] = (),
        cards: Iterable[Card] = (),
        fees: Iterable[Fee] = (),
        transactions: Iterable[Transaction] = (),
        disputes: Iterable[Dispute] = (),
    ) -> None:
        self.state = BankState(
            customers={c.id: c for c in customers},
            cards={c.id: c for c in cards},
            fees={f.id: f for f in fees},
            transactions={t.id: t for t in transactions},
            disputes={d.id: d for d in disputes},
        )
        self.lock = asyncio.Lock()

    def unit_of_work(self) -> "InMemoryUnitOfWork":
        return InMemoryUnitOfWork(self)


class InMemoryUnitOfWork:
    customers: "InMemoryCustomerRepository"
    cards: "InMemoryCardRepository"
    pending_actions: "InMemoryPendingActionRepository"
    fees: "InMemoryFeeRepository"
    transactions: "InMemoryTransactionRepository"
    disputes: "InMemoryDisputeRepository"
    escalations: "InMemoryEscalationRepository"

    def __init__(self, bank: InMemoryBank) -> None:
        self._bank = bank
        self._working = BankState()

    async def __aenter__(self) -> Self:
        await self._bank.lock.acquire()
        self._working = copy.deepcopy(self._bank.state)
        self.customers = InMemoryCustomerRepository(self._working.customers)
        self.cards = InMemoryCardRepository(self._working.cards)
        self.pending_actions = InMemoryPendingActionRepository(self._working.pending_actions)
        self.fees = InMemoryFeeRepository(self._working.fees)
        self.transactions = InMemoryTransactionRepository(self._working.transactions)
        self.disputes = InMemoryDisputeRepository(self._working.disputes)
        self.escalations = InMemoryEscalationRepository(self._working.escalations)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._bank.lock.release()

    async def commit(self) -> None:
        self._bank.state = self._working


class InMemoryCustomerRepository:
    def __init__(self, customers: dict[CustomerId, Customer]) -> None:
        self._customers = customers

    async def get(self, customer_id: CustomerId) -> Customer | None:
        return self._customers.get(customer_id)


class InMemoryCardRepository:
    def __init__(self, cards: dict[CardId, Card]) -> None:
        self._cards = cards

    async def get(self, card_id: CardId) -> Card | None:
        return self._cards.get(card_id)

    async def list_for(self, customer_id: CustomerId) -> list[Card]:
        return [card for card in self._cards.values() if card.customer_id == customer_id]

    async def save(self, card: Card) -> None:
        self._cards[card.id] = card


class InMemoryPendingActionRepository:
    def __init__(self, pending_actions: dict[PendingActionId, PendingAction]) -> None:
        self._pending_actions = pending_actions

    async def get(self, pending_action_id: PendingActionId) -> PendingAction | None:
        return self._pending_actions.get(pending_action_id)

    async def list_for(self, customer_id: CustomerId) -> list[PendingAction]:
        return [a for a in self._pending_actions.values() if a.customer_id == customer_id]

    async def save(self, pending_action: PendingAction) -> None:
        self._pending_actions[pending_action.id] = pending_action


class InMemoryFeeRepository:
    def __init__(self, fees: dict[FeeId, Fee]) -> None:
        self._fees = fees

    async def add(self, fee: Fee) -> None:
        self._fees[fee.id] = fee


class InMemoryTransactionRepository:
    def __init__(self, transactions: dict[TransactionId, Transaction]) -> None:
        self._transactions = transactions

    async def get(self, transaction_id: TransactionId) -> Transaction | None:
        return self._transactions.get(transaction_id)

    async def list_for(self, customer_id: CustomerId) -> list[Transaction]:
        return [t for t in self._transactions.values() if t.customer_id == customer_id]


class InMemoryDisputeRepository:
    def __init__(self, disputes: dict[DisputeId, Dispute]) -> None:
        self._disputes = disputes

    async def list_for(self, customer_id: CustomerId) -> list[Dispute]:
        return [d for d in self._disputes.values() if d.customer_id == customer_id]

    async def add(self, dispute: Dispute) -> None:
        self._disputes[dispute.id] = dispute


class InMemoryEscalationRepository:
    def __init__(self, escalations: dict[EscalationId, Escalation]) -> None:
        self._escalations = escalations

    async def add(self, escalation: Escalation) -> None:
        self._escalations[escalation.id] = escalation
