"""The bank as a service: its MCP tools over Streamable HTTP for the agent, and the Customer
Channel over HTTP for the Customer's app (ADR-0021).

Composition root: Postgres repositories, migrated and seeded with the demo Customer on
startup. Settings come from the environment (see .env.example).
"""

import asyncio
import os
import sys

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from sparring.bank.adapters.clock import SystemClock
from sparring.bank.adapters.customer_channel import AppSessions, customer_channel_routes
from sparring.bank.adapters.mcp_server import SessionTokens, build_mcp_server
from sparring.bank.adapters.postgres.migrate import migrate_async
from sparring.bank.adapters.postgres.repositories import PostgresBank
from sparring.bank.application.bank import Bank
from sparring.bank.application.ports import Clock
from sparring.demo import ANA, ANA_CARDS, ana_transactions

REQUIRED = ("SPARRING_BANK_DATABASE_URL", "SPARRING_SESSION_SECRET", "SPARRING_APP_TOKEN")


def create_app(
    bank: Bank, tokens: SessionTokens, clock: Clock, sessions: AppSessions, hosts: list[str]
) -> Starlette:
    """`hosts` are the Host headers the MCP endpoint accepts, against DNS rebinding."""
    security = TransportSecuritySettings(allowed_hosts=hosts)
    app = build_mcp_server(bank, tokens, clock).streamable_http_app(transport_security=security)
    app.router.routes.extend(customer_channel_routes(bank, sessions))
    app.router.routes.append(Route("/health", _healthy))
    return app


async def _healthy(request: Request) -> Response:
    return PlainTextResponse("ok")


async def serve() -> None:
    url = os.environ["SPARRING_BANK_DATABASE_URL"]
    await migrate_async(url)
    postgres = PostgresBank.connect(url)
    clock = SystemClock()
    async with postgres.unit_of_work() as uow:
        seeded = await uow.customers.get(ANA.id) is not None
    if not seeded:
        await postgres.load([ANA], ANA_CARDS, transactions=ana_transactions(clock.now().date()))
    bank = Bank(postgres.unit_of_work, clock)
    tokens = SessionTokens(os.environ["SPARRING_SESSION_SECRET"].encode())
    sessions = AppSessions({os.environ["SPARRING_APP_TOKEN"]: ANA.id})
    port = int(os.environ.get("SPARRING_BANK_PORT", "8001"))
    hosts = os.environ.get("SPARRING_BANK_HOSTS", f"localhost:{port},bank:{port}").split(",")
    app = create_app(bank, tokens, clock, sessions, hosts)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port))
    try:
        await server.serve()
    finally:
        await postgres.dispose()


def main() -> None:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} (see .env.example).")
    asyncio.run(serve())
