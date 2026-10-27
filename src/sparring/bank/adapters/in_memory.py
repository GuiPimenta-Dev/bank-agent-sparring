import asyncio
import copy
from collections.abc import Iterable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

from sparring.bank.domain.card import Card, CardId
from sparring.bank.domain.customer import Customer, CustomerId


@dataclass
class BankState:
    customers: dict[CustomerId, Customer] = field(default_factory=dict[CustomerId, Customer])
    cards: dict[CardId, Card] = field(default_factory=dict[CardId, Card])


class InMemoryBank:
    """Bank state held in memory; each simulated conversation gets its own (ADR-0009)."""

    def __init__(self, customers: Iterable[Customer] = (), cards: Iterable[Card] = ()) -> None:
        self.state = BankState({c.id: c for c in customers}, {c.id: c for c in cards})
        # ponytail: one lock serializes every Unit of Work on this bank; fine for one
        # conversation per bank, per-aggregate locking if a bank ever serves many.
        self.lock = asyncio.Lock()

    def unit_of_work(self) -> "InMemoryUnitOfWork":
        return InMemoryUnitOfWork(self)


class InMemoryUnitOfWork:
    customers: "InMemoryCustomerRepository"
    cards: "InMemoryCardRepository"

    def __init__(self, bank: InMemoryBank) -> None:
        self._bank = bank
        self._working = BankState()

    async def __aenter__(self) -> Self:
        await self._bank.lock.acquire()
        self._working = copy.deepcopy(self._bank.state)
        self.customers = InMemoryCustomerRepository(self._working.customers)
        self.cards = InMemoryCardRepository(self._working.cards)
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
