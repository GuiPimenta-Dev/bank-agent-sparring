"""The repository contract: one suite, run against every repository adapter (ADR-0009).

The Postgres leg needs a database: set SPARRING_TEST_DATABASE_URL, for example to the
throwaway one in infra/postgres. Without it, the leg is skipped; CI always runs it.
"""

import os
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from sparring.bank.adapters.clock import FixedClock
from sparring.bank.adapters.in_memory import InMemoryBank
from sparring.bank.adapters.postgres.migrate import downgrade, migrate
from sparring.bank.adapters.postgres.repositories import PostgresBank
from sparring.bank.adapters.postgres.schema import metadata
from sparring.bank.application.bank import Bank
from sparring.bank.application.ports import UnitOfWork
from sparring.bank.domain.card import Card, CardId, FreezeSource, LossReason
from sparring.bank.domain.customer import Customer, CustomerId, IdentityClaim, Tier
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
from sparring.bank.domain.refusals import DisputeWindowExpired
from sparring.bank.domain.transaction import Transaction, TransactionId

pytestmark = pytest.mark.anyio

DATABASE = os.environ.get("SPARRING_TEST_DATABASE_URL")
NOON = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
ANA = Customer(CustomerId("C-1001"), "Ana Souza", date(1990, 5, 17), "SW1A 1AA", Tier.STANDARD)
BEN = Customer(CustomerId("C-2002"), "Ben Okafor", date(1984, 1, 2), "M1 1AE", Tier.PREMIUM)
DEBIT = Card(CardId("card-ana-debit"), ANA.id, "4242")
CREDIT = Card(CardId("card-ana-credit"), ANA.id, "0005", FreezeSource.FRAUD_TEAM)
BEN_DEBIT = Card(CardId("card-ben-debit"), BEN.id, "1881")
PRET = Transaction(
    TransactionId("tx-ana-pret"),
    ANA.id,
    DEBIT.id,
    "Pret A Manger",
    Money(Decimal("42.50")),
    date(2026, 10, 21),
)
OLD = Transaction(
    TransactionId("tx-ana-old"),
    ANA.id,
    DEBIT.id,
    "Ryanair",
    Money(Decimal("212.30")),
    date(2026, 8, 27),
)
PENDING = Transaction(
    TransactionId("tx-ana-pending"), ANA.id, DEBIT.id, "Ticketline", Money(Decimal("64.00"))
)

UnitOfWorkFactory = Callable[[], UnitOfWork]


@pytest.fixture(scope="session")
def database() -> Iterator[str]:
    """A database migrated from scratch, once per test session."""
    if DATABASE is None:
        if os.environ.get("CI"):
            pytest.fail("CI must run the Postgres leg: set SPARRING_TEST_DATABASE_URL")
        pytest.skip("SPARRING_TEST_DATABASE_URL is not set")
    # Alembic runs an event loop of its own, which cannot nest in the test's.
    with ThreadPoolExecutor(1) as thread:
        thread.submit(downgrade, DATABASE).result()
        thread.submit(migrate, DATABASE).result()
    yield DATABASE


@pytest.fixture(params=["in_memory", "postgres"])
async def unit_of_work(request: pytest.FixtureRequest) -> AsyncIterator[UnitOfWorkFactory]:
    customers, cards, transactions = [ANA, BEN], [DEBIT, CREDIT, BEN_DEBIT], [PRET, OLD, PENDING]
    if request.param == "in_memory":
        yield InMemoryBank(customers, cards, transactions=transactions).unit_of_work
        return
    url: str = request.getfixturevalue("database")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        tables = ", ".join(table.name for table in metadata.sorted_tables)
        await connection.execute(text(f"TRUNCATE {tables}"))
    bank = PostgresBank(engine)
    await bank.load(customers, cards, transactions=transactions)
    yield bank.unit_of_work
    await bank.dispose()


async def test_customers_are_found_by_id(unit_of_work: UnitOfWorkFactory) -> None:
    async with unit_of_work() as uow:
        assert await uow.customers.get(ANA.id) == ANA
        assert await uow.customers.get(CustomerId("C-9999")) is None


async def test_cards_are_read_and_listed_per_customer(unit_of_work: UnitOfWorkFactory) -> None:
    async with unit_of_work() as uow:
        assert await uow.cards.get(CREDIT.id) == CREDIT
        assert await uow.cards.get(CardId("card-nobody")) is None
        assert sorted(await uow.cards.list_for(ANA.id), key=lambda c: c.id) == [CREDIT, DEBIT]


async def test_a_saved_card_is_read_back_after_commit(unit_of_work: UnitOfWorkFactory) -> None:
    async with unit_of_work() as uow:
        card = await uow.cards.get(DEBIT.id)
        assert card is not None
        card.freeze(FreezeSource.CUSTOMER)
        replacement = Card(CardId("card-ana-new"), ANA.id, "7777", replaces=DEBIT.id)
        await uow.cards.save(card)
        await uow.cards.save(replacement)
        await uow.commit()

    async with unit_of_work() as uow:
        saved = await uow.cards.get(DEBIT.id)
        assert saved is not None and saved.frozen_by is FreezeSource.CUSTOMER
        assert await uow.cards.get(replacement.id) == replacement


async def test_changes_without_a_commit_are_discarded(unit_of_work: UnitOfWorkFactory) -> None:
    async with unit_of_work() as uow:
        card = await uow.cards.get(DEBIT.id)
        assert card is not None
        card.freeze(FreezeSource.CUSTOMER)
        await uow.cards.save(card)

    async with unit_of_work() as uow:
        unchanged = await uow.cards.get(DEBIT.id)
        assert unchanged is not None and unchanged.frozen_by is None


async def test_an_exception_discards_the_unit_of_work(unit_of_work: UnitOfWorkFactory) -> None:
    with pytest.raises(RuntimeError):
        async with unit_of_work() as uow:
            await uow.escalations.add(Escalation(EscalationId("esc-1"), "A human, please."))
            raise RuntimeError("the tool call failed")

    async with unit_of_work() as uow:
        assert await uow.escalations.get(EscalationId("esc-1")) is None


async def test_transactions_are_read_and_listed_per_customer(
    unit_of_work: UnitOfWorkFactory,
) -> None:
    async with unit_of_work() as uow:
        assert await uow.transactions.get(PENDING.id) == PENDING
        assert await uow.transactions.get(TransactionId("tx-nobody")) is None
        listed = sorted(await uow.transactions.list_for(ANA.id), key=lambda t: t.id)
        assert listed == [OLD, PENDING, PRET]
        assert await uow.transactions.list_for(BEN.id) == []


async def test_disputes_fees_and_escalations_are_added_and_read_back(
    unit_of_work: UnitOfWorkFactory,
) -> None:
    dispute = Dispute(DisputeId("dispute-tx-ana-pret"), ANA.id, PRET.id, Money(Decimal("42.50")))
    no_credit = Dispute(DisputeId("dispute-tx-ana-old"), ANA.id, OLD.id, None)
    fee = Fee(FeeId("fee-1"), ANA.id, Money(Decimal("10.00")), "Replacement Card")
    escalation = Escalation(EscalationId("esc-1"), "Three transactions not recognised.")
    async with unit_of_work() as uow:
        await uow.disputes.add(dispute)
        await uow.disputes.add(no_credit)
        await uow.fees.add(fee)
        await uow.escalations.add(escalation)
        await uow.commit()

    async with unit_of_work() as uow:
        disputes = sorted(await uow.disputes.list_for(ANA.id), key=lambda d: d.id)
        assert disputes == [no_credit, dispute]
        assert await uow.disputes.list_for(BEN.id) == []
        assert await uow.fees.list_for(ANA.id) == [fee]
        assert await uow.escalations.get(escalation.id) == escalation


async def test_pending_actions_keep_their_operation_and_status(
    unit_of_work: UnitOfWorkFactory,
) -> None:
    report = PendingAction(
        PendingActionId("pa-1"),
        ANA.id,
        ReportLostOrStolen(DEBIT.id, LossReason.STOLEN),
        "Cancel the debit card ending 4242.",
        NOON + timedelta(minutes=10),
    )
    dispute = PendingAction(
        PendingActionId("pa-2"),
        ANA.id,
        DisputeTransaction(PRET.id),
        "Dispute £42.50 to Pret A Manger.",
        NOON + timedelta(minutes=10),
    )
    async with unit_of_work() as uow:
        await uow.pending_actions.save(report)
        await uow.pending_actions.save(dispute)
        await uow.commit()
    async with unit_of_work() as uow:
        rejected = await uow.pending_actions.get(report.id)
        assert rejected == report
        assert rejected is not None
        rejected.reject(NOON)
        await uow.pending_actions.save(rejected)
        await uow.commit()

    async with unit_of_work() as uow:
        listed = sorted(await uow.pending_actions.list_for(ANA.id), key=lambda a: a.id)
        assert [(a.operation, a.status) for a in listed] == [
            (report.operation, PendingActionStatus.REJECTED),
            (dispute.operation, PendingActionStatus.AWAITING),
        ]
        assert await uow.pending_actions.get(PendingActionId("pa-none")) is None


async def test_a_confirmation_commits_all_of_its_changes_at_once(
    unit_of_work: UnitOfWorkFactory,
) -> None:
    bank = Bank(unit_of_work, FixedClock(NOON))
    session = await bank.verify_identity(IdentityClaim(ANA.id, ANA.date_of_birth, ANA.postcode))
    action = await bank.request_lost_or_stolen_report(session, DEBIT.id, LossReason.LOST)

    await bank.confirm(ANA.id, action.id)

    async with unit_of_work() as uow:
        cancelled = await uow.cards.get(DEBIT.id)
        assert cancelled is not None and cancelled.cancelled_because is LossReason.LOST
        cards = await uow.cards.list_for(ANA.id)
        assert any(card.replaces == DEBIT.id for card in cards)
        assert [fee.amount for fee in await uow.fees.list_for(ANA.id)] == [Money(Decimal("10.00"))]
        confirmed = await uow.pending_actions.get(action.id)
        assert confirmed is not None and confirmed.status is PendingActionStatus.CONFIRMED


async def test_a_confirmation_a_hard_rule_refuses_changes_nothing(
    unit_of_work: UnitOfWorkFactory,
) -> None:
    clock = FixedClock(datetime(2026, 10, 26, 23, 55, tzinfo=UTC))
    bank = Bank(unit_of_work, clock)
    session = await bank.verify_identity(IdentityClaim(ANA.id, ANA.date_of_birth, ANA.postcode))
    action = await bank.request_dispute(session, OLD.id)
    clock.advance(timedelta(minutes=6))

    with pytest.raises(DisputeWindowExpired):
        await bank.confirm(ANA.id, action.id)

    async with unit_of_work() as uow:
        assert await uow.disputes.list_for(ANA.id) == []
        unanswered = await uow.pending_actions.get(action.id)
        assert unanswered is not None and unanswered.status is PendingActionStatus.AWAITING


# Postgres only: the schema itself


async def test_the_migrations_build_exactly_the_schema_the_adapter_uses(database: str) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = create_async_engine(database)
    async with engine.connect() as connection:
        differences = await connection.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), metadata)
        )
    await engine.dispose()

    assert differences == []


async def test_the_database_also_refuses_a_second_dispute_for_one_transaction(
    database: str,
) -> None:
    engine = create_async_engine(database)
    async with engine.begin() as connection:
        tables = ", ".join(table.name for table in metadata.sorted_tables)
        await connection.execute(text(f"TRUNCATE {tables}"))
    bank = PostgresBank(engine)
    await bank.load(customers=[ANA], cards=[DEBIT], transactions=[PRET])
    first = Dispute(DisputeId("d-1"), ANA.id, PRET.id, None)
    second = Dispute(DisputeId("d-2"), ANA.id, PRET.id, None)

    with pytest.raises(IntegrityError):
        async with bank.unit_of_work() as uow:
            await uow.disputes.add(first)
            await uow.disputes.add(second)
            await uow.commit()
    await bank.dispose()
