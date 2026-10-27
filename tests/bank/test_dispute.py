from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from mcp import Client
from mcp_types import CallToolResult, RequestParamsMeta

from sparring.bank.adapters.clock import FixedClock
from sparring.bank.adapters.in_memory import InMemoryBank
from sparring.bank.adapters.mcp_server import SESSION_META_KEY, SessionTokens, build_mcp_server
from sparring.bank.application.bank import Bank
from sparring.bank.domain.card import Card, CardId
from sparring.bank.domain.customer import Customer, CustomerId, Tier
from sparring.bank.domain.dispute import Dispute, DisputeId, ensure_disputable, open_dispute
from sparring.bank.domain.money import Money
from sparring.bank.domain.pending_action import PendingActionId
from sparring.bank.domain.refusals import (
    DisputeAlreadyOpen,
    DisputeWindowExpired,
    TransactionPending,
)
from sparring.bank.domain.transaction import Transaction, TransactionId

ANA = Customer(CustomerId("C-1001"), "Ana Souza", date(1990, 5, 17), "SW1A 1AA", Tier.STANDARD)
BEN = Customer(CustomerId("C-2002"), "Ben Okafor", date(1984, 1, 2), "M1 1AE", Tier.PREMIUM)
DEBIT = CardId("card-ana-debit")
NOON = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
TODAY = date(2026, 10, 26)
PAYMENT = TransactionId("tx-ana-1")


def payment(
    amount: str = "42.50", settled_on: date | None = date(2026, 10, 21), owner: Customer = ANA
) -> Transaction:
    return Transaction(
        PAYMENT, owner.id, DEBIT, "Pret A Manger", Money(Decimal(amount)), settled_on
    )


def credit(amount: str, customer: Customer = ANA) -> Money | None:
    return open_dispute(payment(amount, owner=customer), customer, [], NOON).provisional_credit


# The domain


def test_a_pending_transaction_cannot_be_disputed() -> None:
    with pytest.raises(TransactionPending):
        ensure_disputable(payment(settled_on=None), [], NOON)


def test_the_window_includes_the_60th_day_after_settlement_and_not_the_61st() -> None:
    ensure_disputable(payment(settled_on=TODAY - timedelta(days=60)), [], NOON)
    with pytest.raises(DisputeWindowExpired):
        ensure_disputable(payment(settled_on=TODAY - timedelta(days=61)), [], NOON)


def test_the_window_closes_at_london_midnight_not_utc_midnight() -> None:
    settled_on = date(2026, 8, 1)
    day_60_in_utc_but_61_in_london = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)
    with pytest.raises(DisputeWindowExpired):
        ensure_disputable(payment(settled_on=settled_on), [], day_60_in_utc_but_61_in_london)


def test_a_transaction_can_have_only_one_open_dispute() -> None:
    disputed = Dispute(DisputeId("dispute-tx-ana-1"), ANA.id, PAYMENT, None)
    with pytest.raises(DisputeAlreadyOpen):
        ensure_disputable(payment(), [disputed], NOON)


def test_provisional_credit_is_the_full_amount_up_to_the_tier_limit() -> None:
    assert credit("500.00") == Money(Decimal("500.00"))
    assert credit("500.01") is None
    assert credit("1000.00", BEN) == Money(Decimal("1000.00"))
    assert credit("1000.01", BEN) is None


def test_money_in_different_currencies_cannot_be_compared() -> None:
    with pytest.raises(ValueError, match="EUR"):
        assert Money(Decimal("1"), "EUR") <= Money(Decimal("2"))


# The agent requests; only the Customer Channel confirms


class Kestrel:
    def __init__(
        self, *transactions: Transaction, disputes: tuple[Dispute, ...] = (), now: datetime = NOON
    ) -> None:
        self.clock = FixedClock(now)
        self.state = InMemoryBank(
            [ANA, BEN], [Card(DEBIT, ANA.id, "4242")], transactions=transactions, disputes=disputes
        )
        self.bank = Bank(self.state.unit_of_work, self.clock)
        self.server = build_mcp_server(self.bank, SessionTokens(b"test-secret"), self.clock)


async def verify(client: Client) -> RequestParamsMeta:
    details = {"customer_number": "C-1001", "date_of_birth": "1990-05-17", "postcode": "SW1A 1AA"}
    result = await client.call_tool("verify_identity", details)
    assert result.meta
    return {SESSION_META_KEY: result.meta[SESSION_META_KEY]}


def action_id(result: CallToolResult) -> PendingActionId:
    return PendingActionId(result.structured_content["pending_action_id"])


async def dispute(client: Client, session: RequestParamsMeta) -> CallToolResult:
    return await client.call_tool("dispute_transaction", {"transaction_id": PAYMENT}, meta=session)


@pytest.mark.anyio
async def test_the_agent_sees_whether_each_transaction_is_pending_or_settled() -> None:
    async with Client(Kestrel(payment()).server) as client:
        result = await client.call_tool("list_transactions", {}, meta=await verify(client))

    assert result.structured_content == {
        "transactions": [
            {
                "transaction_id": "tx-ana-1",
                "card_id": "card-ana-debit",
                "merchant": "Pret A Manger",
                "amount": "42.50",
                "currency": "GBP",
                "status": "settled",
                "settled_on": "2026-10-21",
            }
        ]
    }


@pytest.mark.anyio
async def test_requesting_a_dispute_holds_a_pending_action_and_opens_nothing_yet() -> None:
    kestrel = Kestrel(payment())
    async with Client(kestrel.server) as client:
        result = await dispute(client, await verify(client))

    assert result.structured_content["status"] == "awaiting_confirmation"
    assert "Provisional Credit of £42.50" in result.structured_content["summary"]
    assert not kestrel.state.state.disputes


@pytest.mark.anyio
async def test_confirming_opens_the_dispute_with_its_provisional_credit() -> None:
    kestrel = Kestrel(payment())
    async with Client(kestrel.server) as client:
        result = await dispute(client, await verify(client))
    await kestrel.bank.confirm(ANA.id, action_id(result))

    assert list(kestrel.state.state.disputes.values()) == [
        Dispute(DisputeId("dispute-tx-ana-1"), ANA.id, PAYMENT, Money(Decimal("42.50")))
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("transaction", "disputes", "rule"),
    [
        (payment(settled_on=None), (), "TransactionPending"),
        (payment(settled_on=TODAY - timedelta(days=61)), (), "DisputeWindowExpired"),
        (payment(), (Dispute(DisputeId("d-1"), ANA.id, PAYMENT, None),), "DisputeAlreadyOpen"),
        (payment(owner=BEN), (), "TransactionNotFound"),
    ],
)
async def test_an_ineligible_dispute_is_refused_when_requested(
    transaction: Transaction, disputes: tuple[Dispute, ...], rule: str
) -> None:
    async with Client(Kestrel(transaction, disputes=disputes).server) as client:
        result = await dispute(client, await verify(client))

    assert result.is_error and result.structured_content["rule"] == rule


@pytest.mark.anyio
async def test_a_window_that_closes_before_the_confirmation_opens_nothing() -> None:
    five_to_midnight = datetime(2026, 10, 26, 23, 55, tzinfo=UTC)
    kestrel = Kestrel(payment(settled_on=TODAY - timedelta(days=60)), now=five_to_midnight)
    async with Client(kestrel.server) as client:
        result = await dispute(client, await verify(client))
    kestrel.clock.advance(timedelta(minutes=6))

    with pytest.raises(DisputeWindowExpired):
        await kestrel.bank.confirm(ANA.id, action_id(result))
    assert not kestrel.state.state.disputes


@pytest.mark.anyio
async def test_two_requests_for_one_transaction_open_one_dispute() -> None:
    kestrel = Kestrel(payment())
    async with Client(kestrel.server) as client:
        session = await verify(client)
        first, second = await dispute(client, session), await dispute(client, session)
    await kestrel.bank.confirm(ANA.id, action_id(first))

    with pytest.raises(DisputeAlreadyOpen):
        await kestrel.bank.confirm(ANA.id, action_id(second))
    assert len(kestrel.state.state.disputes) == 1
