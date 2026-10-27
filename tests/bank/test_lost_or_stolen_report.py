from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from mcp import Client
from mcp_types import CallToolResult, RequestParamsMeta

from sparring.bank.adapters.clock import FixedClock
from sparring.bank.adapters.in_memory import InMemoryBank
from sparring.bank.adapters.mcp_server import SESSION_META_KEY, SessionTokens, build_mcp_server
from sparring.bank.application.bank import Bank
from sparring.bank.domain.card import Card, CardId, FreezeSource, LossReason
from sparring.bank.domain.customer import Customer, CustomerId, Tier
from sparring.bank.domain.lost_or_stolen_report import execute_lost_or_stolen_report
from sparring.bank.domain.money import Money
from sparring.bank.domain.pending_action import PendingActionId
from sparring.bank.domain.refusals import (
    CardCancelled,
    PendingActionAlreadyAnswered,
    PendingActionExpired,
    PendingActionNotFound,
)

ANA = Customer(CustomerId("C-1001"), "Ana Souza", date(1990, 5, 17), "SW1A 1AA", Tier.STANDARD)
BEN = Customer(CustomerId("C-2002"), "Ben Okafor", date(1984, 1, 2), "M1 1AE", Tier.PREMIUM)
DEBIT = CardId("card-ana-debit")
NOON = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def debit_card() -> Card:
    return Card(DEBIT, ANA.id, "4242")


class Kestrel:
    """A bank with its MCP server and Ana's Customer Channel, on a clock the test controls."""

    def __init__(self, *cards: Card) -> None:
        self.clock = FixedClock(NOON)
        self.state = InMemoryBank([ANA, BEN], cards)
        self.bank = Bank(self.state.unit_of_work, self.clock)
        self.server = build_mcp_server(self.bank, SessionTokens(b"test-secret"), self.clock)


async def verify(client: Client, customer: Customer = ANA) -> RequestParamsMeta:
    details = {
        "customer_number": customer.id,
        "date_of_birth": customer.date_of_birth.isoformat(),
        "postcode": customer.postcode,
    }
    result = await client.call_tool("verify_identity", details)
    assert result.meta
    return {SESSION_META_KEY: result.meta[SESSION_META_KEY]}


async def report_lost(client: Client, session: RequestParamsMeta) -> PendingActionId:
    result = await client.call_tool(
        "report_lost_or_stolen", {"card_id": DEBIT, "reason": "lost"}, meta=session
    )
    assert not result.is_error, result.content
    return PendingActionId(result.structured_content["pending_action_id"])


def body(result: CallToolResult) -> dict[str, str]:
    return result.structured_content


# The domain


def test_cancelling_a_card_issues_a_replacement_with_a_new_number() -> None:
    card = debit_card()
    replacement = card.cancel(LossReason.STOLEN)
    assert card.cancelled_because is LossReason.STOLEN
    assert replacement.replaces == DEBIT and replacement.customer_id == ANA.id
    assert replacement.last_four != card.last_four
    assert debit_card().cancel(LossReason.LOST) == replacement


def test_a_cancelled_card_can_be_neither_frozen_nor_cancelled_again() -> None:
    card = debit_card()
    card.cancel(LossReason.LOST)
    with pytest.raises(CardCancelled):
        card.freeze(FreezeSource.CUSTOMER)
    with pytest.raises(CardCancelled):
        card.cancel(LossReason.LOST)


def test_the_replacement_fee_depends_on_the_tier() -> None:
    _, standard_fee = execute_lost_or_stolen_report(debit_card(), ANA, LossReason.LOST)
    _, premium_fee = execute_lost_or_stolen_report(debit_card(), BEN, LossReason.LOST)
    assert standard_fee is not None and standard_fee.amount == Money(Decimal("10.00"))
    assert premium_fee is None


# The agent requests; only the Customer Channel confirms


@pytest.mark.anyio
async def test_reporting_holds_a_pending_action_and_changes_nothing_yet() -> None:
    kestrel = Kestrel(debit_card())
    async with Client(kestrel.server) as client:
        session = await verify(client)
        action_id = await report_lost(client, session)
        status = await client.call_tool(
            "get_pending_action", {"pending_action_id": action_id}, meta=session
        )

    assert body(status)["status"] == "awaiting_confirmation"
    assert "for £10.00" in body(status)["summary"]
    assert kestrel.state.state.cards[DEBIT].cancelled_because is None
    assert [a.id for a in await kestrel.bank.awaiting_confirmation(ANA.id)] == [action_id]


@pytest.mark.anyio
async def test_the_agent_has_no_tool_to_confirm() -> None:
    async with Client(Kestrel().server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert not {name for name in names if "confirm" in name or "reject" in name}


@pytest.mark.anyio
async def test_confirming_cancels_the_card_issues_the_replacement_and_charges_the_fee() -> None:
    kestrel = Kestrel(debit_card())
    async with Client(kestrel.server) as client:
        session = await verify(client)
        action_id = await report_lost(client, session)
        await kestrel.bank.confirm(ANA.id, action_id)
        status = await client.call_tool(
            "get_pending_action", {"pending_action_id": action_id}, meta=session
        )

    state = kestrel.state.state
    assert state.cards[DEBIT].cancelled_because is LossReason.LOST
    assert state.cards[CardId("card-ana-debit-replacement")].replaces == DEBIT
    assert [fee.amount for fee in state.fees.values()] == [Money(Decimal("10.00"))]
    assert body(status)["status"] == "confirmed"


@pytest.mark.anyio
async def test_rejecting_leaves_the_card_untouched() -> None:
    kestrel = Kestrel(debit_card())
    async with Client(kestrel.server) as client:
        action_id = await report_lost(client, await verify(client))
    await kestrel.bank.reject(ANA.id, action_id)

    assert kestrel.state.state.cards[DEBIT].cancelled_because is None
    assert not kestrel.state.state.fees
    with pytest.raises(PendingActionAlreadyAnswered):
        await kestrel.bank.confirm(ANA.id, action_id)


@pytest.mark.anyio
async def test_an_expired_pending_action_can_no_longer_be_confirmed() -> None:
    kestrel = Kestrel(debit_card())
    async with Client(kestrel.server) as client:
        session = await verify(client)
        action_id = await report_lost(client, session)
        kestrel.clock.advance(timedelta(minutes=10))
        status = await client.call_tool(
            "get_pending_action", {"pending_action_id": action_id}, meta=session
        )

    assert body(status)["status"] == "expired"
    assert await kestrel.bank.awaiting_confirmation(ANA.id) == []
    with pytest.raises(PendingActionExpired):
        await kestrel.bank.confirm(ANA.id, action_id)
    assert kestrel.state.state.cards[DEBIT].cancelled_because is None


@pytest.mark.anyio
async def test_another_customers_pending_action_does_not_exist_for_them() -> None:
    kestrel = Kestrel(debit_card())
    async with Client(kestrel.server) as client:
        action_id = await report_lost(client, await verify(client))
        ben = await verify(client, BEN)
        status = await client.call_tool(
            "get_pending_action", {"pending_action_id": action_id}, meta=ben
        )

    assert body(status)["rule"] == "PendingActionNotFound"
    with pytest.raises(PendingActionNotFound):
        await kestrel.bank.confirm(BEN.id, action_id)


@pytest.mark.anyio
async def test_a_confirmation_that_fails_changes_nothing() -> None:
    kestrel = Kestrel(debit_card())
    async with Client(kestrel.server) as client:
        session = await verify(client)
        first = await report_lost(client, session)
        second = await report_lost(client, session)
    await kestrel.bank.confirm(ANA.id, first)

    with pytest.raises(CardCancelled):
        await kestrel.bank.confirm(ANA.id, second)
    assert [a.id for a in await kestrel.bank.awaiting_confirmation(ANA.id)] == [second]
    assert len(kestrel.state.state.fees) == 1


@pytest.mark.anyio
async def test_a_cancelled_card_cannot_be_reported_again() -> None:
    async with Client(
        Kestrel(Card(DEBIT, ANA.id, "4242", cancelled_because=LossReason.LOST)).server
    ) as client:
        result = await client.call_tool(
            "report_lost_or_stolen",
            {"card_id": DEBIT, "reason": "stolen"},
            meta=await verify(client),
        )
    assert body(result)["rule"] == "CardCancelled"
