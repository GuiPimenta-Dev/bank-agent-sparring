"""Terminal chat: a person plays the Customer and talks to the support agent.

Composition root: wires an in-memory Kestrel Bank, its MCP server, and the agent,
which reaches the bank only through an in-process MCP client.
"""

import asyncio
import os
import secrets
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

from mcp import Client

from sparring.agent.support_agent import SupportAgent
from sparring.bank.adapters.clock import SystemClock
from sparring.bank.adapters.in_memory import InMemoryBank
from sparring.bank.adapters.mcp_server import SessionTokens, build_mcp_server
from sparring.bank.application.bank import Bank
from sparring.bank.domain.card import Card, CardId, FreezeSource
from sparring.bank.domain.customer import Customer, CustomerId, Tier
from sparring.bank.domain.money import Money
from sparring.bank.domain.transaction import Transaction, TransactionId
from sparring.tracing import langfuse_client

ANA = Customer(CustomerId("C-1001"), "Ana Souza", date(1990, 5, 17), "SW1A 1AA", Tier.STANDARD)
ANA_DEBIT = CardId("card-ana-debit")
ANA_CARDS = [
    Card(ANA_DEBIT, ANA.id, "4242"),
    Card(CardId("card-ana-credit"), ANA.id, "0005", FreezeSource.FRAUD_TEAM),
]


def ana_transactions(today: date) -> list[Transaction]:
    """Settled recently, still pending, over the Provisional Credit limit, and too old."""
    rows = [
        ("tx-ana-1", "Pret A Manger", "8.45", 2),
        ("tx-ana-2", "Ticketline", "64.00", None),
        ("tx-ana-3", "Currys", "749.00", 10),
        ("tx-ana-4", "Ryanair", "212.30", 75),
    ]
    return [
        Transaction(
            TransactionId(transaction_id),
            ANA.id,
            ANA_DEBIT,
            merchant,
            Money(Decimal(amount)),
            today - timedelta(days=age) if age is not None else None,
        )
        for transaction_id, merchant, amount, age in rows
    ]


BANNER = f"""Kestrel Bank card support. Type 'exit' to leave.
You are {ANA.full_name}: customer number {ANA.id}, born {ANA.date_of_birth:%Y-%m-%d}, \
postcode {ANA.postcode}. Your credit card was frozen by the fraud team, and your \
debit card has four recent transactions you could dispute.
Requests the agent makes on your behalf appear below as your Kestrel Bank app.
"""


async def chat() -> None:
    tracing = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))
    if tracing:
        langfuse_client()
    clock = SystemClock()
    state = InMemoryBank([ANA], ANA_CARDS, transactions=ana_transactions(clock.now().date()))
    bank = Bank(state.unit_of_work, clock)
    server = build_mcp_server(bank, SessionTokens(secrets.token_bytes(32)), clock)
    async with Client(server) as client:
        agent = await SupportAgent.connect(
            client,
            model=os.environ.get("SPARRING_AGENT_MODEL", "claude-haiku-5-5"),
            effort=os.environ.get("SPARRING_AGENT_EFFORT"),
            tracing=tracing,
        )
        conversation_id = uuid.uuid4().hex
        print(BANNER)
        while True:
            try:
                message = await asyncio.to_thread(input, "you> ")
            except EOFError:
                break
            if message.strip().lower() in {"exit", "quit"}:
                break
            turn = await agent.reply(conversation_id, message)
            print(f"agent> {turn.reply}\n")
            if state.state.escalations:
                print("[A human support agent takes over the conversation from here.]")
                break
            await customer_channel(bank)


async def customer_channel(bank: Bank) -> None:
    """The Customer's app: the only place a Pending Action can be confirmed (ADR-0004)."""
    for action in await bank.awaiting_confirmation(ANA.id):
        print(f"[Kestrel Bank app] {action.summary}")
        answer = await asyncio.to_thread(input, "[Kestrel Bank app] Confirm? [y/N] ")
        if answer.strip().lower() == "y":
            await bank.confirm(ANA.id, action.id)
            print("[Kestrel Bank app] Confirmed.\n")
        else:
            await bank.reject(ANA.id, action.id)
            print("[Kestrel Bank app] Rejected.\n")


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY (see .env.example) to talk to the agent.")
    asyncio.run(chat())
