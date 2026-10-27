"""The bank service and the agent's API, driven over HTTP with a scripted agent."""

import asyncio
import json
import os
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import uvicorn
from fakes import calls, says, scripted
from langchain_core.messages import AIMessage
from mcp import Client

from sparring.agent.support_agent import SupportAgent, persistent_memory
from sparring.agent_api import Answer, create_app
from sparring.bank.adapters.clock import FixedClock
from sparring.bank.adapters.customer_channel import AppSessions
from sparring.bank.adapters.in_memory import InMemoryBank
from sparring.bank.adapters.mcp_server import SessionTokens, build_mcp_server
from sparring.bank.application.bank import Bank
from sparring.bank.domain.card import LossReason
from sparring.bank.domain.customer import IdentityClaim
from sparring.bank.domain.pending_action import PendingActionId
from sparring.bank.domain.refusals import HardRuleRefusal
from sparring.bank.domain.transaction import TransactionId
from sparring.bank_service import create_app as create_bank_app
from sparring.demo import ANA, ANA_CARDS, ANA_DEBIT, ana_transactions

pytestmark = pytest.mark.anyio

NOON = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
APP_TOKEN = "demo-app-token"
VERIFY = calls(
    "verify_identity", customer_number="C-1001", date_of_birth="1990-05-17", postcode="SW1A 1AA"
)


class Kestrel:
    def __init__(self) -> None:
        self.clock = FixedClock(NOON)
        self.state = InMemoryBank([ANA], ANA_CARDS, transactions=ana_transactions(NOON.date()))
        self.bank = Bank(self.state.unit_of_work, self.clock)
        self.tokens = SessionTokens(b"test-secret")


# The bank service


def bank_service(kestrel: Kestrel, hosts: list[str]) -> Any:
    sessions = AppSessions({APP_TOKEN: ANA.id})
    return create_bank_app(kestrel.bank, kestrel.tokens, kestrel.clock, sessions, hosts)


async def test_the_customer_channel_serves_only_a_signed_in_customer() -> None:
    app = bank_service(Kestrel(), ["test"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as http:
        anonymous = await http.get("/channel/pending-actions")
        signed_in = await http.get(
            "/channel/pending-actions", headers={"Authorization": f"Bearer {APP_TOKEN}"}
        )

    assert anonymous.status_code == 401
    assert signed_in.json() == {"pending_actions": []}


async def test_the_customer_confirms_and_rejects_through_the_channel() -> None:
    kestrel = Kestrel()
    session = await kestrel.bank.verify_identity(
        IdentityClaim(ANA.id, ANA.date_of_birth, ANA.postcode)
    )
    report = await kestrel.bank.request_lost_or_stolen_report(session, ANA_DEBIT, LossReason.LOST)
    dispute = await kestrel.bank.request_dispute(session, TransactionId("tx-ana-1"))
    app = bank_service(kestrel, ["test"])
    signed_in = {"Authorization": f"Bearer {APP_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as http:
        confirmed = await http.post(
            f"/channel/pending-actions/{report.id}/confirm", headers=signed_in
        )
        rejected = await http.post(
            f"/channel/pending-actions/{dispute.id}/reject", headers=signed_in
        )
        again = await http.post(f"/channel/pending-actions/{report.id}/confirm", headers=signed_in)
        nonsense = await http.post(f"/channel/pending-actions/{report.id}/maybe", headers=signed_in)

    assert (confirmed.status_code, rejected.status_code) == (204, 204)
    assert kestrel.state.state.cards[ANA_DEBIT].cancelled_because is LossReason.LOST
    assert not kestrel.state.state.disputes
    assert again.status_code == 409 and again.json()["rule"] == "PendingActionAlreadyAnswered"
    assert nonsense.status_code == 404


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_the_agent_reaches_the_bank_s_tools_over_streamable_http() -> None:
    kestrel = Kestrel()
    port = free_port()
    app = bank_service(kestrel, [f"127.0.0.1:{port}"])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            agent = await SupportAgent.with_model(
                client,
                scripted(VERIFY, calls("freeze_card", card_id=ANA_DEBIT), says("Frozen.")),
            )
            turn = await agent.reply("conversation-1", "Freeze my debit card.")
    finally:
        server.should_exit = True
        await serving

    assert turn.reply == "Frozen."
    assert kestrel.state.state.cards[ANA_DEBIT].frozen_by is not None


# The agent's API


class InProcessApp:
    """The demo Customer's app, answering through the bank directly."""

    def __init__(self, bank: Bank) -> None:
        self._bank = bank

    async def awaiting(self) -> list[dict[str, Any]]:
        actions = await self._bank.awaiting_confirmation(ANA.id)
        return [{"id": a.id, "summary": a.summary} for a in actions]

    async def answer(self, action_id: str, answer: Answer) -> dict[str, Any] | None:
        respond = self._bank.confirm if answer == "confirm" else self._bank.reject
        try:
            await respond(ANA.id, PendingActionId(action_id))
        except HardRuleRefusal as refusal:
            return {"rule": type(refusal).__name__, "message": str(refusal)}
        return None


@asynccontextmanager
async def api_with(kestrel: Kestrel, *script: AIMessage) -> AsyncGenerator[httpx.AsyncClient]:
    server = build_mcp_server(kestrel.bank, kestrel.tokens, kestrel.clock)
    async with Client(server) as client:
        agent = await SupportAgent.with_model(client, scripted(*script))
        api = create_app(agent, InProcessApp(kestrel.bank))
        transport = httpx.ASGITransport(api)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


def events(stream: str) -> list[tuple[str, dict[str, str]]]:
    parsed: list[tuple[str, dict[str, str]]] = []
    for raw in stream.strip().split("\n\n"):
        name, data = (line.split(": ", 1)[1] for line in raw.splitlines())
        parsed.append((name, json.loads(data)))
    return parsed


async def test_a_reply_streams_as_server_sent_events_and_joins_the_history() -> None:
    script = [says("Hello Ana, how can I help with your cards today?")]
    async with api_with(Kestrel(), *script) as http:
        conversation = (await http.post("/api/conversations")).json()["id"]
        stream = await http.post(
            f"/api/conversations/{conversation}/messages", json={"text": "Hi there"}
        )
        history = (await http.get(f"/api/conversations/{conversation}/messages")).json()

    sent = events(stream.text)
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "".join(data["text"] for name, data in sent if name == "delta") == script[0].text
    assert sent[-1] == ("done", {"reply": script[0].text})
    assert history == {
        "messages": [
            {"speaker": "customer", "text": "Hi there"},
            {"speaker": "agent", "text": script[0].text},
        ]
    }


async def test_the_page_s_app_panel_confirms_what_the_agent_requested() -> None:
    kestrel = Kestrel()
    script = [
        VERIFY,
        calls("report_lost_or_stolen", card_id=ANA_DEBIT, reason="lost"),
        says("Please confirm the report in your Kestrel Bank app."),
    ]
    async with api_with(kestrel, *script) as http:
        conversation = (await http.post("/api/conversations")).json()["id"]
        await http.post(
            f"/api/conversations/{conversation}/messages",
            json={"text": "I lost my debit card. C-1001, 1990-05-17, SW1A 1AA."},
        )
        [request] = (await http.get("/api/app/pending-actions")).json()["pending_actions"]
        confirmed = await http.post(f"/api/app/pending-actions/{request['id']}/confirm")
        again = await http.post(f"/api/app/pending-actions/{request['id']}/confirm")
        page = await http.get("/")

    assert "for £10.00" in request["summary"]
    assert confirmed.status_code == 204
    assert kestrel.state.state.cards[ANA_DEBIT].cancelled_because is not None
    assert again.status_code == 409 and again.json()["rule"] == "PendingActionAlreadyAnswered"
    assert "Your Kestrel Bank app" in page.text


async def test_a_message_must_say_something() -> None:
    async with api_with(Kestrel()) as http:
        response = await http.post("/api/conversations/c-1/messages", json={"text": ""})

    assert response.status_code == 422


DATABASE = os.environ.get("SPARRING_TEST_DATABASE_URL")


@pytest.mark.skipif(DATABASE is None, reason="SPARRING_TEST_DATABASE_URL is not set")
async def test_a_conversation_survives_a_restart_of_the_agent() -> None:
    assert DATABASE is not None
    url = DATABASE.replace("+asyncpg", "")
    kestrel = Kestrel()
    server = build_mcp_server(kestrel.bank, kestrel.tokens, kestrel.clock)
    conversation = f"restart-{os.getpid()}-{datetime.now(UTC).timestamp()}"

    async with Client(server) as client, persistent_memory(url) as memory:
        before = await SupportAgent.with_model(client, scripted(says("Noted.")), memory=memory)
        await before.reply(conversation, "Remember me.")
    async with Client(server) as client, persistent_memory(url) as memory:
        after = await SupportAgent.with_model(client, scripted(), memory=memory)
        history = await after.history(conversation)

    assert history == [("customer", "Remember me."), ("agent", "Noted.")]
