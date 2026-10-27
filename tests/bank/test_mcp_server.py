from datetime import UTC, date, datetime, timedelta

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, RequestParamsMeta, TextContent

from sparring.bank.adapters.clock import FixedClock
from sparring.bank.adapters.in_memory import InMemoryBank, InMemoryUnitOfWork
from sparring.bank.adapters.mcp_server import SESSION_META_KEY, SessionTokens, build_mcp_server
from sparring.bank.application.bank import Bank
from sparring.bank.domain.card import Card, CardId, FreezeSource
from sparring.bank.domain.customer import Customer, CustomerId, Tier
from sparring.bank.domain.verified_session import VerifiedSession

pytestmark = pytest.mark.anyio

ANA = Customer(CustomerId("C-1001"), "Ana Souza", date(1990, 5, 17), "SW1A 1AA", Tier.STANDARD)
BEN = Customer(CustomerId("C-2002"), "Ben Okafor", date(1984, 1, 2), "M1 1AE", Tier.PREMIUM)
ANA_DEBIT = Card(CardId("card-ana-debit"), ANA.id, "4242")
BEN_DEBIT = Card(CardId("card-ben-debit"), BEN.id, "1881")
ANA_DETAILS = {"customer_number": "C-1001", "date_of_birth": "1990-05-17", "postcode": "sw1a 1aa"}


def kestrel(*cards: Card, clock: FixedClock | None = None) -> MCPServer:
    bank = InMemoryBank([ANA, BEN], cards)
    clock = clock or FixedClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    return build_mcp_server(Bank(bank.unit_of_work, clock), SessionTokens(b"test-secret"), clock)


async def verify(client: Client) -> RequestParamsMeta:
    result = await client.call_tool("verify_identity", ANA_DETAILS)
    assert not result.is_error and result.meta
    return {SESSION_META_KEY: result.meta[SESSION_META_KEY]}


def refused_by(result: CallToolResult) -> str:
    assert result.is_error
    return result.structured_content["rule"]


async def test_verified_customer_freezes_their_card() -> None:
    async with Client(kestrel(ANA_DEBIT)) as client:
        session = await verify(client)
        freeze = await client.call_tool("freeze_card", {"card_id": "card-ana-debit"}, meta=session)
        cards = await client.call_tool("list_cards", {}, meta=session)

    assert not freeze.is_error
    assert cards.structured_content == {
        "cards": [
            {
                "card_id": "card-ana-debit",
                "last_four": "4242",
                "status": "frozen",
                "frozen_by": "customer",
                "replaces": None,
            }
        ]
    }


async def test_account_tools_refuse_before_identity_verification() -> None:
    async with Client(kestrel(ANA_DEBIT)) as client:
        listing = await client.call_tool("list_cards", {})
        freeze = await client.call_tool("freeze_card", {"card_id": "card-ana-debit"})

    assert refused_by(listing) == refused_by(freeze) == "IdentityNotVerified"


async def test_escalation_needs_no_identity_verification() -> None:
    state = InMemoryBank([ANA], [ANA_DEBIT])
    clock = FixedClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    server = build_mcp_server(Bank(state.unit_of_work, clock), SessionTokens(b"s"), clock)
    async with Client(server) as client:
        result = await client.call_tool("escalate", {"reason": "Wants to complain to a human."})

    assert not result.is_error
    assert [e.reason for e in state.state.escalations.values()] == ["Wants to complain to a human."]


async def test_wrong_identity_details_open_no_verified_session() -> None:
    async with Client(kestrel(ANA_DEBIT)) as client:
        result = await client.call_tool("verify_identity", {**ANA_DETAILS, "postcode": "SW1A 2AA"})

    assert refused_by(result) == "IdentityVerificationFailed"
    assert SESSION_META_KEY not in (result.meta or {})


async def test_a_fraud_team_freeze_cannot_be_lifted_but_a_customer_freeze_can() -> None:
    fraud_frozen = Card(CardId("card-ana-credit"), ANA.id, "0005", FreezeSource.FRAUD_TEAM)
    customer_frozen = Card(CardId("card-ana-debit"), ANA.id, "4242", FreezeSource.CUSTOMER)
    async with Client(kestrel(fraud_frozen, customer_frozen)) as client:
        session = await verify(client)
        unfreeze = "unfreeze_card"
        locked = await client.call_tool(unfreeze, {"card_id": "card-ana-credit"}, meta=session)
        lifted = await client.call_tool(unfreeze, {"card_id": "card-ana-debit"}, meta=session)

    assert refused_by(locked) == "FraudFreezeLocked"
    assert not lifted.is_error


async def test_another_customers_card_does_not_exist_for_the_session() -> None:
    async with Client(kestrel(ANA_DEBIT, BEN_DEBIT)) as client:
        session = await verify(client)
        result = await client.call_tool("freeze_card", {"card_id": "card-ben-debit"}, meta=session)

    assert refused_by(result) == "CardNotFound"


async def test_an_expired_verified_session_is_refused() -> None:
    clock = FixedClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    async with Client(kestrel(ANA_DEBIT, clock=clock)) as client:
        session = await verify(client)
        clock.advance(timedelta(minutes=31))
        result = await client.call_tool("list_cards", {}, meta=session)

    assert refused_by(result) == "VerifiedSessionExpired"


async def test_a_session_token_not_signed_by_the_bank_is_refused() -> None:
    forged = SessionTokens(b"attacker-key").issue(
        VerifiedSession(BEN.id, datetime(2030, 1, 1, tzinfo=UTC))
    )
    async with Client(kestrel(BEN_DEBIT)) as client:
        result = await client.call_tool("list_cards", {}, meta={SESSION_META_KEY: forged})

    assert refused_by(result) == "IdentityNotVerified"


async def test_no_tool_exposes_a_customer_id_or_the_session_token() -> None:
    async with Client(kestrel()) as client:
        tools = (await client.list_tools()).tools

    parameters = {name for tool in tools for name in tool.input_schema.get("properties", {})}
    assert parameters == {
        "customer_number",
        "date_of_birth",
        "postcode",
        "card_id",
        "reason",
        "pending_action_id",
        "transaction_id",
    }


async def test_verification_tells_the_agent_the_customers_tier() -> None:
    async with Client(kestrel()) as client:
        result = await client.call_tool("verify_identity", ANA_DETAILS)

    assert result.structured_content == {"verified": True, "tier": "standard"}


async def test_invalid_arguments_tell_the_model_what_to_fix() -> None:
    async with Client(kestrel(ANA_DEBIT)) as client:
        session = await verify(client)
        result = await client.call_tool(
            "report_lost_or_stolen",
            {"card_id": "card-ana-debit", "reason": "misplaced"},
            meta=session,
        )

    assert result.is_error
    text = result.content[0].text if isinstance(result.content[0], TextContent) else ""
    assert "reason" in text and "'lost' or 'stolen'" in text


async def test_an_unexpected_failure_reaches_the_model_without_internals() -> None:
    def broken_unit_of_work() -> InMemoryUnitOfWork:
        raise RuntimeError("connection to postgres://bank:hunter2@db failed")

    clock = FixedClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    server = build_mcp_server(Bank(broken_unit_of_work, clock), SessionTokens(b"secret"), clock)
    async with Client(server) as client:
        result = await client.call_tool("verify_identity", ANA_DETAILS)

    assert result.is_error
    assert result.content == [TextContent(type="text", text="Error executing tool verify_identity")]
