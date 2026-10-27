from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from mcp import Client

from sparring.agent.support_agent import SupportAgent
from sparring.bank.adapters.clock import FixedClock
from sparring.bank.adapters.in_memory import InMemoryBank
from sparring.bank.adapters.mcp_server import SessionTokens, build_mcp_server
from sparring.bank.application.bank import Bank
from sparring.bank.domain.card import Card, CardId, FreezeSource
from sparring.bank.domain.customer import Customer, CustomerId

pytestmark = pytest.mark.anyio

ANA = Customer(CustomerId("C-1001"), "Ana Souza", date(1990, 5, 17), "SW1A 1AA")
ANA_DEBIT = CardId("card-ana-debit")


class ScriptedModel(GenericFakeChatModel):
    """Replays scripted messages; LangChain requires subclassing to accept tools."""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


def calls(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name}])


async def test_the_agent_freezes_a_card_through_the_bank_tools() -> None:
    bank = InMemoryBank([ANA], [Card(ANA_DEBIT, ANA.id, "4242")])
    clock = FixedClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    server = build_mcp_server(Bank(bank.unit_of_work, clock), SessionTokens(b"test-secret"))
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
    server = build_mcp_server(Bank(bank.unit_of_work, clock), SessionTokens(b"test-secret"))
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
