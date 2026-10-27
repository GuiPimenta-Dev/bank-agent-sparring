"""The system under test for one Scenario: a fresh Kestrel Bank seeded from the
Scenario's Bank Snapshot, its MCP server, and an agent connected to it in-process.

Shared by the composition roots that run Scenarios and by their tests.
"""

import copy
import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from mcp import Client

from sparring.agent.support_agent import SupportAgent
from sparring.bank.adapters.clock import FixedClock
from sparring.bank.adapters.in_memory import InMemoryBank
from sparring.bank.adapters.mcp_server import SessionTokens, build_mcp_server
from sparring.bank.application.bank import Bank
from sparring.evaluation.conversation import SystemFactory, SystemUnderTest
from sparring.evaluation.scenario import BankSnapshot, Scenario

AgentFactory = Callable[[Client], Awaitable[SupportAgent]]


class KestrelUnderTest:
    def __init__(self, agent: SupportAgent, bank: InMemoryBank, conversation_id: str) -> None:
        self._agent = agent
        self._bank = bank
        self._conversation_id = conversation_id

    async def reply(self, message: str) -> str:
        return (await self._agent.reply(self._conversation_id, message)).reply

    def bank_snapshot(self) -> BankSnapshot:
        state = copy.deepcopy(self._bank.state)
        return BankSnapshot(list(state.customers.values()), list(state.cards.values()))


def kestrel(connect_agent: AgentFactory) -> SystemFactory:
    @asynccontextmanager
    async def open_system(
        scenario: Scenario, conversation_id: str
    ) -> AsyncGenerator[SystemUnderTest]:
        snapshot = copy.deepcopy(scenario.bank)
        bank = InMemoryBank(snapshot.customers, snapshot.cards)
        server = build_mcp_server(
            Bank(bank.unit_of_work, FixedClock(scenario.now)),
            SessionTokens(secrets.token_bytes(32)),
        )
        async with Client(server) as client:
            yield KestrelUnderTest(await connect_agent(client), bank, conversation_id)

    return open_system
