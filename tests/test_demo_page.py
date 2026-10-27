"""The demo page end to end in a real browser, with a scripted agent: the same flow the GIF
recorder drives against the real one."""

import asyncio
import os

import pytest
import uvicorn
from demo_flow import confirmation_flow
from fakes import calls, says, scripted
from mcp import Client
from playwright.async_api import Error, async_playwright
from test_services import VERIFY, InProcessApp, Kestrel, free_port

from sparring.agent.support_agent import SupportAgent
from sparring.agent_api import create_app
from sparring.bank.adapters.mcp_server import build_mcp_server
from sparring.bank.domain.card import LossReason
from sparring.demo import ANA_DEBIT

pytestmark = pytest.mark.anyio


async def test_the_page_takes_the_customer_from_verification_to_a_confirmed_report() -> None:
    kestrel = Kestrel()
    script = [
        VERIFY,
        calls("report_lost_or_stolen", card_id=ANA_DEBIT, reason="lost"),
        says("I've requested the report. Please confirm it in your Kestrel Bank app."),
        says("You're welcome, Ana."),
    ]
    port = free_port()
    async with Client(build_mcp_server(kestrel.bank, kestrel.tokens, kestrel.clock)) as client:
        agent = await SupportAgent.with_model(client, scripted(*script))
        app = create_app(agent, InProcessApp(kestrel.bank))
        server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
        serving = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        try:
            async with async_playwright() as playwright:
                try:
                    browser = await playwright.chromium.launch()
                except Error as missing:
                    if os.environ.get("CI"):
                        raise
                    pytest.skip(f"No browser for Playwright: {missing.message.splitlines()[0]}")
                page = await browser.new_page()
                await confirmation_flow(page, f"http://127.0.0.1:{port}")
                await browser.close()
        finally:
            server.should_exit = True
            await serving

    assert kestrel.state.state.cards[ANA_DEBIT].cancelled_because is LossReason.LOST
