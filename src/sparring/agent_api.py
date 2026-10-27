"""The agent's API and the two-panel demo page (ADR-0021).

The page shows the chat with the agent next to the demo Customer's Kestrel Bank app, where
the Pending Actions the agent requested wait for her Confirmation (ADR-0004). The app talks
to the bank's Customer Channel, never to the agent.

Composition root: the agent on Claude, reaching the bank's MCP tools over Streamable HTTP,
with conversations kept in Postgres so they survive a restart. Each agent turn is one
Langfuse trace, grouped by conversation (ADR-0012). Settings come from the environment
(see .env.example).
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from mcp import Client
from pydantic import BaseModel, Field

from sparring.agent.support_agent import AgentUnavailable, SupportAgent, persistent_memory
from sparring.tracing import langfuse_client, tracing_enabled

PAGE = (Path(__file__).parent / "demo.html").read_text()
logger = logging.getLogger(__name__)
REQUIRED = (
    "ANTHROPIC_API_KEY",
    "SPARRING_BANK_URL",
    "SPARRING_AGENT_DATABASE_URL",
    "SPARRING_APP_TOKEN",
)

Answer = Literal["confirm", "reject"]


class CustomerApp(Protocol):
    """The demo Customer's Kestrel Bank app, as the page's second panel shows it."""

    async def awaiting(self) -> list[dict[str, Any]]:
        """What awaits her Confirmation right now."""
        ...

    async def answer(self, action_id: str, answer: Answer) -> dict[str, Any] | None:
        """None when the bank took the answer; otherwise the Hard Rule that refused it."""
        ...


class BankCustomerApp:
    """The app signed in to the bank's Customer Channel over HTTP."""

    def __init__(self, bank_url: str, token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=bank_url, headers={"Authorization": f"Bearer {token}"}
        )

    async def awaiting(self) -> list[dict[str, Any]]:
        response = await self._client.get("/channel/pending-actions")
        response.raise_for_status()
        return response.json()["pending_actions"]

    async def answer(self, action_id: str, answer: Answer) -> dict[str, Any] | None:
        response = await self._client.post(f"/channel/pending-actions/{action_id}/{answer}")
        if response.status_code == 409:
            return response.json()
        response.raise_for_status()
        return None


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)


def create_app(agent: SupportAgent, app: CustomerApp) -> FastAPI:
    api = FastAPI(title="Kestrel Bank card support")

    @api.get("/", response_class=HTMLResponse)
    async def page() -> str:
        return PAGE

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post("/api/conversations")
    async def start() -> dict[str, str]:
        return {"id": uuid.uuid4().hex}

    @api.get("/api/conversations/{conversation_id}/messages")
    async def history(conversation_id: str) -> dict[str, list[dict[str, str]]]:
        said = await agent.history(conversation_id)
        return {"messages": [{"speaker": speaker, "text": text} for speaker, text in said]}

    @api.post("/api/conversations/{conversation_id}/messages")
    async def send(conversation_id: str, message: Message) -> StreamingResponse:
        return StreamingResponse(
            _events(agent, conversation_id, message.text), media_type="text/event-stream"
        )

    @api.get("/api/app/pending-actions")
    async def awaiting() -> dict[str, list[dict[str, Any]]]:
        return {"pending_actions": await app.awaiting()}

    @api.post("/api/app/pending-actions/{action_id}/{answer}")
    async def answer(action_id: str, answer: Answer) -> Response:
        refusal = await app.answer(action_id, answer)
        if refusal is not None:
            return JSONResponse(refusal, status_code=409)
        return Response(status_code=204)

    return api


async def _events(agent: SupportAgent, conversation_id: str, text: str) -> AsyncIterator[str]:
    """Server-Sent Events: the reply as it is written, then the whole reply."""
    try:
        async for delta in agent.stream(conversation_id, text):
            yield _event("delta", {"text": delta})
    except AgentUnavailable:
        yield _event("error", {"message": "The agent is unavailable right now. Try again soon."})
        return
    except Exception:
        # Nothing about the failure reaches the page (ADR-0017); the log keeps it.
        logger.exception("The agent failed on conversation %s", conversation_id)
        yield _event("error", {"message": "Something went wrong. Please try again."})
        return
    said = await agent.history(conversation_id)
    reply = next((text for speaker, text in reversed(said) if speaker == "agent"), "")
    yield _event("done", {"reply": reply})


def _event(name: str, data: dict[str, str]) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


async def serve() -> None:
    tracing = tracing_enabled()
    if tracing:
        langfuse_client()
    bank_url = os.environ["SPARRING_BANK_URL"]
    async with (
        Client(f"{bank_url}/mcp") as client,
        persistent_memory(os.environ["SPARRING_AGENT_DATABASE_URL"]) as memory,
    ):
        agent = await SupportAgent.connect(
            client,
            os.environ.get("SPARRING_AGENT_MODEL", "claude-haiku-5-5"),
            os.environ.get("SPARRING_AGENT_EFFORT") or None,
            tracing=tracing,
            memory=memory,
        )
        app = create_app(agent, BankCustomerApp(bank_url, os.environ["SPARRING_APP_TOKEN"]))
        port = int(os.environ.get("SPARRING_API_PORT", "8000"))
        await uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port)).serve()


def main() -> None:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} (see .env.example).")
    asyncio.run(serve())
