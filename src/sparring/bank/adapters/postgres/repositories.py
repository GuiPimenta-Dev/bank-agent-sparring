"""Postgres repositories: rows mapped to aggregates by hand, SQLAlchemy Core only
(ADR-0015). One Unit of Work is one database transaction, opened per tool call (ADR-0009).
"""

from collections.abc import Iterable
from types import TracebackType
from typing import Any, Self

from sqlalchemy import Row, insert, select
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncTransaction,
    create_async_engine,
)

from sparring.bank.adapters.postgres import schema
from sparring.bank.domain.card import Card, CardId, FreezeSource, LossReason
from sparring.bank.domain.customer import Customer, CustomerId, Tier
from sparring.bank.domain.dispute import Dispute, DisputeId
from sparring.bank.domain.escalation import Escalation, EscalationId
from sparring.bank.domain.fee import Fee, FeeId
from sparring.bank.domain.money import Money
from sparring.bank.domain.pending_action import (
    DisputeTransaction,
    PendingAction,
    PendingActionId,
    PendingActionStatus,
    ReportLostOrStolen,
)
from sparring.bank.domain.transaction import Transaction, TransactionId


class PostgresBank:
    """Bank state in Postgres, shared by every conversation that reaches the bank."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @classmethod
    def connect(cls, url: str) -> "PostgresBank":
        return cls(create_async_engine(url))

    def unit_of_work(self) -> "PostgresUnitOfWork":
        return PostgresUnitOfWork(self._engine)

    async def load(
        self,
        customers: Iterable[Customer] = (),
        cards: Iterable[Card] = (),
        fees: Iterable[Fee] = (),
        transactions: Iterable[Transaction] = (),
        disputes: Iterable[Dispute] = (),
    ) -> None:
        """Seeds the bank, as the in-memory bank's constructor does, in one transaction."""
        async with self._engine.begin() as connection:
            for table, rows in (
                (schema.customers, [_customer_row(c) for c in customers]),
                (schema.cards, [_card_row(c) for c in cards]),
                (schema.transactions, [_transaction_row(t) for t in transactions]),
                (schema.fees, [_fee_row(f) for f in fees]),
                (schema.disputes, [_dispute_row(d) for d in disputes]),
            ):
                if rows:
                    await connection.execute(insert(table), rows)

    async def dispose(self) -> None:
        await self._engine.dispose()


class PostgresUnitOfWork:
    customers: "PostgresCustomerRepository"
    cards: "PostgresCardRepository"
    pending_actions: "PostgresPendingActionRepository"
    fees: "PostgresFeeRepository"
    transactions: "PostgresTransactionRepository"
    disputes: "PostgresDisputeRepository"
    escalations: "PostgresEscalationRepository"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._connection: AsyncConnection | None = None
        self._transaction: AsyncTransaction | None = None

    async def __aenter__(self) -> Self:
        connection = self._connection = await self._engine.connect()
        self._transaction = await connection.begin()
        self.customers = PostgresCustomerRepository(connection)
        self.cards = PostgresCardRepository(connection)
        self.pending_actions = PostgresPendingActionRepository(connection)
        self.fees = PostgresFeeRepository(connection)
        self.transactions = PostgresTransactionRepository(connection)
        self.disputes = PostgresDisputeRepository(connection)
        self.escalations = PostgresEscalationRepository(connection)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._connection is not None and self._transaction is not None
        if self._transaction.is_active:
            await self._transaction.rollback()
        await self._connection.close()

    async def commit(self) -> None:
        assert self._transaction is not None
        await self._transaction.commit()


class PostgresCustomerRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, customer_id: CustomerId) -> Customer | None:
        query = select(schema.customers).where(schema.customers.c.id == customer_id)
        row = (await self._connection.execute(query)).one_or_none()
        return _customer(row) if row else None


class PostgresCardRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, card_id: CardId) -> Card | None:
        """Locks the card until the Unit of Work ends, since it may be saved back."""
        query = select(schema.cards).where(schema.cards.c.id == card_id).with_for_update()
        row = (await self._connection.execute(query)).one_or_none()
        return _card(row) if row else None

    async def list_for(self, customer_id: CustomerId) -> list[Card]:
        query = (
            select(schema.cards)
            .where(schema.cards.c.customer_id == customer_id)
            .order_by(schema.cards.c.id)
        )
        return [_card(row) for row in await self._connection.execute(query)]

    async def save(self, card: Card) -> None:
        row = _card_row(card)
        statement = upsert(schema.cards).values(row)
        await self._connection.execute(
            statement.on_conflict_do_update(index_elements=["id"], set_=row)
        )


class PostgresPendingActionRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, pending_action_id: PendingActionId) -> PendingAction | None:
        """Locks the Pending Action until the Unit of Work ends, so two Confirmations of it
        cannot both go through."""
        table = schema.pending_actions
        query = select(table).where(table.c.id == pending_action_id).with_for_update()
        row = (await self._connection.execute(query)).one_or_none()
        return _pending_action(row) if row else None

    async def list_for(self, customer_id: CustomerId) -> list[PendingAction]:
        table = schema.pending_actions
        query = select(table).where(table.c.customer_id == customer_id).order_by(table.c.id)
        return [_pending_action(row) for row in await self._connection.execute(query)]

    async def save(self, pending_action: PendingAction) -> None:
        row = _pending_action_row(pending_action)
        statement = upsert(schema.pending_actions).values(row)
        await self._connection.execute(
            statement.on_conflict_do_update(index_elements=["id"], set_=row)
        )


class PostgresFeeRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def list_for(self, customer_id: CustomerId) -> list[Fee]:
        query = (
            select(schema.fees)
            .where(schema.fees.c.customer_id == customer_id)
            .order_by(schema.fees.c.id)
        )
        return [_fee(row) for row in await self._connection.execute(query)]

    async def add(self, fee: Fee) -> None:
        await self._connection.execute(insert(schema.fees).values(_fee_row(fee)))


class PostgresTransactionRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, transaction_id: TransactionId) -> Transaction | None:
        query = select(schema.transactions).where(schema.transactions.c.id == transaction_id)
        row = (await self._connection.execute(query)).one_or_none()
        return _transaction(row) if row else None

    async def list_for(self, customer_id: CustomerId) -> list[Transaction]:
        table = schema.transactions
        query = select(table).where(table.c.customer_id == customer_id).order_by(table.c.id)
        return [_transaction(row) for row in await self._connection.execute(query)]


class PostgresDisputeRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def list_for(self, customer_id: CustomerId) -> list[Dispute]:
        table = schema.disputes
        query = select(table).where(table.c.customer_id == customer_id).order_by(table.c.id)
        return [_dispute(row) for row in await self._connection.execute(query)]

    async def add(self, dispute: Dispute) -> None:
        await self._connection.execute(insert(schema.disputes).values(_dispute_row(dispute)))


class PostgresEscalationRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, escalation_id: EscalationId) -> Escalation | None:
        query = select(schema.escalations).where(schema.escalations.c.id == escalation_id)
        row = (await self._connection.execute(query)).one_or_none()
        return Escalation(EscalationId(row.id), row.reason) if row else None

    async def add(self, escalation: Escalation) -> None:
        row = {"id": escalation.id, "reason": escalation.reason}
        await self._connection.execute(insert(schema.escalations).values(row))


def _customer(row: Row[Any]) -> Customer:
    return Customer(
        CustomerId(row.id), row.full_name, row.date_of_birth, row.postcode, Tier(row.tier)
    )


def _customer_row(customer: Customer) -> dict[str, Any]:
    return {
        "id": customer.id,
        "full_name": customer.full_name,
        "date_of_birth": customer.date_of_birth,
        "postcode": customer.postcode,
        "tier": str(customer.tier),
    }


def _card(row: Row[Any]) -> Card:
    return Card(
        CardId(row.id),
        CustomerId(row.customer_id),
        row.last_four,
        FreezeSource(row.frozen_by) if row.frozen_by else None,
        LossReason(row.cancelled_because) if row.cancelled_because else None,
        CardId(row.replaces) if row.replaces else None,
    )


def _card_row(card: Card) -> dict[str, Any]:
    return {
        "id": card.id,
        "customer_id": card.customer_id,
        "last_four": card.last_four,
        "frozen_by": str(card.frozen_by) if card.frozen_by else None,
        "cancelled_because": str(card.cancelled_because) if card.cancelled_because else None,
        "replaces": card.replaces,
    }


def _transaction(row: Row[Any]) -> Transaction:
    return Transaction(
        TransactionId(row.id),
        CustomerId(row.customer_id),
        CardId(row.card_id),
        row.merchant,
        Money(row.amount, row.currency),
        row.settled_on,
    )


def _transaction_row(transaction: Transaction) -> dict[str, Any]:
    return {
        "id": transaction.id,
        "customer_id": transaction.customer_id,
        "card_id": transaction.card_id,
        "merchant": transaction.merchant,
        "amount": transaction.amount.amount,
        "currency": transaction.amount.currency,
        "settled_on": transaction.settled_on,
    }


def _dispute(row: Row[Any]) -> Dispute:
    credit = row.provisional_credit
    return Dispute(
        DisputeId(row.id),
        CustomerId(row.customer_id),
        TransactionId(row.transaction_id),
        Money(credit, row.currency) if credit is not None else None,
    )


def _dispute_row(dispute: Dispute) -> dict[str, Any]:
    credit = dispute.provisional_credit
    return {
        "id": dispute.id,
        "customer_id": dispute.customer_id,
        "transaction_id": dispute.transaction_id,
        "provisional_credit": credit.amount if credit else None,
        "currency": credit.currency if credit else "GBP",
    }


def _fee(row: Row[Any]) -> Fee:
    return Fee(
        FeeId(row.id), CustomerId(row.customer_id), Money(row.amount, row.currency), row.reason
    )


def _fee_row(fee: Fee) -> dict[str, Any]:
    return {
        "id": fee.id,
        "customer_id": fee.customer_id,
        "amount": fee.amount.amount,
        "currency": fee.amount.currency,
        "reason": fee.reason,
    }


def _pending_action(row: Row[Any]) -> PendingAction:
    operation = (
        ReportLostOrStolen(CardId(row.card_id), LossReason(row.because))
        if row.kind == "report_lost_or_stolen"
        else DisputeTransaction(TransactionId(row.transaction_id))
    )
    return PendingAction(
        PendingActionId(row.id),
        CustomerId(row.customer_id),
        operation,
        row.summary,
        row.expires_at,
        PendingActionStatus(row.status),
    )


def _pending_action_row(action: PendingAction) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": action.id,
        "customer_id": action.customer_id,
        "summary": action.summary,
        "expires_at": action.expires_at,
        "status": str(action.status),
        "card_id": None,
        "because": None,
        "transaction_id": None,
    }
    match action.operation:
        case ReportLostOrStolen(card_id, because):
            return {
                **row,
                "kind": "report_lost_or_stolen",
                "card_id": card_id,
                "because": str(because),
            }
        case DisputeTransaction(transaction_id):
            return {**row, "kind": "dispute_transaction", "transaction_id": transaction_id}
