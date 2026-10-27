from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from sparring.bank.domain.card import Card, CardId
from sparring.bank.domain.customer import Customer, CustomerId


class Clock(Protocol):
    def now(self) -> datetime: ...


class CustomerRepository(Protocol):
    async def get(self, customer_id: CustomerId) -> Customer | None: ...


class CardRepository(Protocol):
    async def get(self, card_id: CardId) -> Card | None: ...

    async def list_for(self, customer_id: CustomerId) -> list[Card]: ...

    async def save(self, card: Card) -> None: ...


class UnitOfWork(Protocol):
    """One atomic unit of bank changes; leaving it without `commit()` discards them."""

    @property
    def customers(self) -> CustomerRepository: ...

    @property
    def cards(self) -> CardRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
