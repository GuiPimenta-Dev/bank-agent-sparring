"""Terminal chat: a person plays the Customer and talks to the support agent.

Composition root: wires an in-memory Kestrel Bank, its MCP server, and the agent,
which reaches the bank only through an in-process MCP client.
"""

import asyncio
import os
import secrets
import sys
import uuid
from datetime import date

from mcp import Client

from sparring.agent.support_agent import SupportAgent
from sparring.bank.adapters.clock import SystemClock
from sparring.bank.adapters.in_memory import InMemoryBank
from sparring.bank.adapters.mcp_server import SessionTokens, build_mcp_server
from sparring.bank.application.bank import Bank
from sparring.bank.domain.card import Card, CardId, FreezeSource
from sparring.bank.domain.customer import Customer, CustomerId

ANA = Customer(CustomerId("C-1001"), "Ana Souza", date(1990, 5, 17), "SW1A 1AA")
ANA_CARDS = [
    Card(CardId("card-ana-debit"), ANA.id, "4242"),
    Card(CardId("card-ana-credit"), ANA.id, "0005", FreezeSource.FRAUD_TEAM),
]

BANNER = f"""Kestrel Bank card support. Type 'exit' to leave.
You are {ANA.full_name}: customer number {ANA.id}, born {ANA.date_of_birth:%Y-%m-%d}, \
postcode {ANA.postcode}. Your credit card was frozen by the fraud team.
"""


async def chat() -> None:
    bank = InMemoryBank([ANA], ANA_CARDS)
    server = build_mcp_server(
        Bank(bank.unit_of_work, SystemClock()), SessionTokens(secrets.token_bytes(32))
    )
    async with Client(server) as client:
        agent = await SupportAgent.connect(
            client,
            model=os.environ.get("SPARRING_AGENT_MODEL", "claude-haiku-5-5"),
            effort=os.environ.get("SPARRING_AGENT_EFFORT"),
            tracing=bool(os.environ.get("LANGFUSE_PUBLIC_KEY")),
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


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY (see .env.example) to talk to the agent.")
    asyncio.run(chat())
