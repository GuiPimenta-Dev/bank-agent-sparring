from datetime import UTC, date, datetime

import anthropic
import httpx2
import pytest
from fakes import ScriptedModel, calls, calls_in_parallel, says, scripted, tool_call, unreachable
from langchain_core.messages import AIMessage
from mcp import Client

from sparring.agent.support_agent import (
    CAP_REACHED_REPLY,
    TOOL_CALL_CAP,
    AgentUnavailable,
    SupportAgent,
    TokenUsage,
)
from sparring.bank.adapters.clock import FixedClock
from sparring.bank.adapters.in_memory import InMemoryBank
from sparring.bank.adapters.mcp_server import SessionTokens, build_mcp_server
from sparring.bank.application.bank import Bank
from sparring.bank.domain.card import Card, CardId, FreezeSource
from sparring.bank.domain.customer import Customer, CustomerId, Tier

pytestmark = pytest.mark.anyio

ANA = Customer(CustomerId("C-1001"), "Ana Souza", date(1990, 5, 17), "SW1A 1AA", Tier.STANDARD)
ANA_DEBIT = CardId("card-ana-debit")


async def test_the_agent_freezes_a_card_through_the_bank_tools() -> None:
    bank = InMemoryBank([ANA], [Card(ANA_DEBIT, ANA.id, "4242")])
    clock = FixedClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    server = build_mcp_server(Bank(bank.unit_of_work, clock), SessionTokens(b"test-secret"), clock)
    script = iter(
        [
            calls(
                "verify_identity",
                customer_number="C-1001",
                date_of_birth="1990-05-17",
                postcode="SW1A 1AA",
            ),
            AIMessage(content="You're verified. Which card should I freeze?"),
            calls("freeze_card", card_id=ANA_DEBIT),
            AIMessage(content="Your debit card is frozen."),
        ]
    )

    async with Client(server) as client:
        agent = await SupportAgent.with_model(client, ScriptedModel(messages=script))
        first = await agent.reply("conversation-1", "Hi, I'm Ana, C-1001, 1990-05-17, SW1A 1AA")
        second = await agent.reply("conversation-1", "Freeze my debit card please")

    assert first.reply == "You're verified. Which card should I freeze?"
    assert second.reply == "Your debit card is frozen."
    assert bank.state.cards[ANA_DEBIT].frozen_by is FreezeSource.CUSTOMER


async def test_a_verified_session_does_not_leak_into_another_conversation() -> None:
    bank = InMemoryBank([ANA], [Card(ANA_DEBIT, ANA.id, "4242")])
    clock = FixedClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    server = build_mcp_server(Bank(bank.unit_of_work, clock), SessionTokens(b"test-secret"), clock)
    script = iter(
        [
            calls(
                "verify_identity",
                customer_number="C-1001",
                date_of_birth="1990-05-17",
                postcode="SW1A 1AA",
            ),
            AIMessage(content="Verified."),
            calls("freeze_card", card_id=ANA_DEBIT),
            AIMessage(content="Done."),
        ]
    )

    async with Client(server) as client:
        agent = await SupportAgent.with_model(client, ScriptedModel(messages=script))
        await agent.reply("ana-conversation", "verify me")
        await agent.reply("someone-else", "freeze card-ana-debit")

    assert bank.state.cards[ANA_DEBIT].frozen_by is None


async def test_each_turn_reports_the_hard_rules_that_refused_it() -> None:
    fraud_frozen = Card(ANA_DEBIT, ANA.id, "4242", FreezeSource.FRAUD_TEAM)
    bank = InMemoryBank([ANA], [fraud_frozen])
    clock = FixedClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    server = build_mcp_server(Bank(bank.unit_of_work, clock), SessionTokens(b"test-secret"), clock)
    script = [
        calls(
            "verify_identity",
            customer_number="C-1001",
            date_of_birth="1990-05-17",
            postcode="SW1A 1AA",
        ),
        calls("unfreeze_card", card_id=ANA_DEBIT),
        says("Only our fraud team can lift that freeze."),
        says("Anything else?"),
    ]

    async with Client(server) as client:
        agent = await SupportAgent.with_model(client, scripted(*script))
        first = await agent.reply(
            "conversation-1", "Unfreeze my card. C-1001, 1990-05-17, SW1A 1AA"
        )
        second = await agent.reply("conversation-1", "Oh, ok.")

    assert [(call.name, call.refused_by) for call in first.tool_calls] == [
        ("verify_identity", None),
        ("unfreeze_card", "FraudFreezeLocked"),
    ]
    assert second.tool_calls == []


def kestrel_with_ana() -> tuple[InMemoryBank, Bank]:
    state = InMemoryBank([ANA], [Card(ANA_DEBIT, ANA.id, "4242")])
    clock = FixedClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    return state, Bank(state.unit_of_work, clock)


async def test_the_tool_call_cap_ends_the_turn_before_the_call_that_would_pass_it() -> None:
    state, bank = kestrel_with_ana()
    server = build_mcp_server(bank, SessionTokens(b"test-secret"), FixedClock(datetime.now(UTC)))
    script = [
        calls(
            "verify_identity",
            customer_number="C-1001",
            date_of_birth="1990-05-17",
            postcode="SW1A 1AA",
        ),
        *[calls("list_cards") for _ in range(TOOL_CALL_CAP - 2)],
        calls_in_parallel(tool_call("list_cards"), tool_call("freeze_card", card_id=ANA_DEBIT)),
        says("Anything else I can help with?"),
    ]

    async with Client(server) as client:
        agent = await SupportAgent.with_model(client, scripted(*script))
        capped = await agent.reply("conversation-1", "Freeze my card. C-1001, 1990-05-17, SW1A 1AA")
        after = await agent.reply("conversation-1", "Hello?")

    assert capped.reply == CAP_REACHED_REPLY
    assert len(capped.tool_calls) == TOOL_CALL_CAP + 1
    assert state.state.cards[ANA_DEBIT].frozen_by is None
    assert after.reply == "Anything else I can help with?"


async def test_a_turn_may_use_the_whole_tool_call_cap() -> None:
    _, bank = kestrel_with_ana()
    server = build_mcp_server(bank, SessionTokens(b"test-secret"), FixedClock(datetime.now(UTC)))
    script = [*[calls("list_cards") for _ in range(TOOL_CALL_CAP)], says("Please verify first.")]

    async with Client(server) as client:
        agent = await SupportAgent.with_model(client, scripted(*script))
        turn = await agent.reply("conversation-1", "What cards do I have?")

    assert turn.reply == "Please verify first."
    assert len(turn.tool_calls) == TOOL_CALL_CAP


async def test_each_turn_adds_up_its_usage_with_cached_input_set_apart() -> None:
    _, bank = kestrel_with_ana()
    server = build_mcp_server(bank, SessionTokens(b"test-secret"), FixedClock(datetime.now(UTC)))
    tool_round = calls("list_cards")
    tool_round.usage_metadata = {
        "input_tokens": 5_000,
        "output_tokens": 40,
        "total_tokens": 5_040,
        "input_token_details": {"cache_read": 3_000, "cache_creation": 1_000},
    }
    answer = says("Please verify first.")
    answer.usage_metadata = {"input_tokens": 600, "output_tokens": 25, "total_tokens": 625}

    async with Client(server) as client:
        agent = await SupportAgent.with_model(client, scripted(tool_round, answer))
        turn = await agent.reply("conversation-1", "What cards do I have?")

    assert turn.usage == TokenUsage(
        input_tokens=1_600, output_tokens=65, cache_read_tokens=3_000, cache_write_tokens=1_000
    )


async def test_a_provider_that_still_fails_after_retries_makes_the_agent_unavailable() -> None:
    _, bank = kestrel_with_ana()
    server = build_mcp_server(bank, SessionTokens(b"test-secret"), FixedClock(datetime.now(UTC)))
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    async with Client(server) as client:
        agent = await SupportAgent.with_model(
            client, unreachable(anthropic.APIConnectionError(request=request))
        )
        with pytest.raises(AgentUnavailable):
            await agent.reply("conversation-1", "Hi")


async def test_an_error_the_provider_would_not_retry_is_not_an_outage() -> None:
    _, bank = kestrel_with_ana()
    server = build_mcp_server(bank, SessionTokens(b"test-secret"), FixedClock(datetime.now(UTC)))
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    bad_request = anthropic.BadRequestError(
        "Your credit balance is too low", response=httpx2.Response(400, request=request), body=None
    )

    async with Client(server) as client:
        agent = await SupportAgent.with_model(client, unreachable(bad_request))
        with pytest.raises(anthropic.BadRequestError):
            await agent.reply("conversation-1", "Hi")
