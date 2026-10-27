"""The system under test for one Scenario: a fresh Kestrel Bank seeded from the
Scenario's Bank Snapshot, its MCP server, an agent connected to it in-process, and the
Persona's Customer Channel.

Shared by the composition roots that run Scenarios and by their tests.
"""

import copy
import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from mcp import Client

from sparring.agent.support_agent import AgentUnavailable, SupportAgent
from sparring.bank.adapters.clock import FixedClock
from sparring.bank.adapters.in_memory import InMemoryBank
from sparring.bank.adapters.mcp_server import SessionTokens, build_mcp_server
from sparring.bank.application.bank import Bank
from sparring.bank.domain.customer import CustomerId
from sparring.bank.domain.pending_action import PendingActionId
from sparring.evaluation.conversation import ConfirmationRequest, SystemFactory, SystemUnderTest
from sparring.evaluation.cost import Usage
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.scenario import BankSnapshot, Scenario
from sparring.evaluation.simulated_user import ToolCall, Turn

AgentFactory = Callable[[Client], Awaitable[SupportAgent]]


class KestrelUnderTest:
    def __init__(
        self,
        agent: SupportAgent,
        bank: Bank,
        state: InMemoryBank,
        app_owner: CustomerId | None,
        conversation_id: str,
        agent_model: str,
    ) -> None:
        self._agent = agent
        self._bank = bank
        self._state = state
        self._app_owner = app_owner
        self._conversation_id = conversation_id
        self._agent_model = agent_model

    async def reply(self, message: str) -> Turn:
        try:
            turn = await self._agent.reply(self._conversation_id, message)
        except AgentUnavailable as error:
            raise InfrastructureFailure(str(error)) from error
        calls = (ToolCall(call.name, call.arguments, call.refused_by) for call in turn.tool_calls)
        usage = Usage(
            self._agent_model,
            turn.usage.input_tokens,
            turn.usage.output_tokens,
            turn.usage.cache_read_tokens,
            turn.usage.cache_write_tokens,
        )
        return Turn("agent", turn.reply, tuple(calls), usage)

    async def confirmation_requests(self) -> list[ConfirmationRequest]:
        """Nothing, for a Persona who does not hold the Customer's app."""
        if self._app_owner is None:
            return []
        actions = await self._bank.awaiting_confirmation(self._app_owner)
        return [ConfirmationRequest(action.id, action.summary) for action in actions]

    async def answer(self, request_id: str, confirm: bool) -> None:
        if self._app_owner is None:
            return
        answer = self._bank.confirm if confirm else self._bank.reject
        await answer(self._app_owner, PendingActionId(request_id))

    def bank_snapshot(self) -> BankSnapshot:
        state = copy.deepcopy(self._state.state)
        return BankSnapshot(
            list(state.customers.values()),
            list(state.cards.values()),
            list(state.fees.values()),
            list(state.transactions.values()),
            list(state.disputes.values()),
            escalated=bool(state.escalations),
        )


def kestrel(connect_agent: AgentFactory, agent_model: str) -> SystemFactory:
    """`agent_model` is the model the agent's usage is priced as."""

    @asynccontextmanager
    async def open_system(
        scenario: Scenario, conversation_id: str
    ) -> AsyncGenerator[SystemUnderTest]:
        snapshot = copy.deepcopy(scenario.bank)
        state = InMemoryBank(
            snapshot.customers,
            snapshot.cards,
            snapshot.fees,
            snapshot.transactions,
            snapshot.disputes,
        )
        clock = FixedClock(scenario.now)
        bank = Bank(state.unit_of_work, clock)
        server = build_mcp_server(bank, SessionTokens(secrets.token_bytes(32)), clock)
        async with Client(server) as client:
            agent = await connect_agent(client)
            app_owner = scenario.persona.customer_id if scenario.played_by_the_customer else None
            yield KestrelUnderTest(agent, bank, state, app_owner, conversation_id, agent_model)

    return open_system
