"""The bank's MCP server: business-level tools over the `Bank` use cases.

The Verified Session travels as a bank-signed token in `_meta`, issued by
`verify_identity` and attached by the agent's process to every later call; it is
in no tool's input schema, so the model never sees it (ADR-0022).
"""

import base64
import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, Field

from sparring.bank.application.bank import Bank
from sparring.bank.domain.card import Card, CardId
from sparring.bank.domain.customer import CustomerId, IdentityClaim
from sparring.bank.domain.refusals import HardRuleRefusal, IdentityNotVerified
from sparring.bank.domain.verified_session import VerifiedSession

SESSION_META_KEY: Final = "bank.kestrel/verified-session"

CardIdArg = Annotated[str, Field(description="The card's id, as returned by list_cards.")]


class SessionTokens:
    """Signs Verified Sessions so they can be carried outside the model's context."""

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def issue(self, session: VerifiedSession) -> str:
        payload = json.dumps(
            {"customer_id": session.customer_id, "expires_at": session.expires_at.isoformat()}
        ).encode()
        return f"{_encode(payload)}.{_encode(self._sign(payload))}"

    def read(self, token: str) -> VerifiedSession:
        try:
            encoded_payload, encoded_signature = token.split(".")
            payload = _decode(encoded_payload)
            if not hmac.compare_digest(_decode(encoded_signature), self._sign(payload)):
                raise ValueError("signature mismatch")
            claims = json.loads(payload)
            return VerifiedSession(
                CustomerId(claims["customer_id"]), datetime.fromisoformat(claims["expires_at"])
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise IdentityNotVerified() from exc

    def _sign(self, payload: bytes) -> bytes:
        return hmac.new(self._secret, payload, hashlib.sha256).digest()


class CardView(BaseModel):
    card_id: str
    last_four: str
    frozen: bool
    frozen_by: str | None

    @classmethod
    def of(cls, card: Card) -> "CardView":
        return cls(
            card_id=card.id,
            last_four=card.last_four,
            frozen=card.frozen_by is not None,
            frozen_by=card.frozen_by,
        )


class CardList(BaseModel):
    cards: list[CardView]


class Refusal(BaseModel):
    refused: Literal[True] = True
    rule: str
    message: str


class Verified(BaseModel):
    verified: Literal[True] = True


def build_mcp_server(bank: Bank, tokens: SessionTokens) -> MCPServer:
    server = MCPServer(
        "Kestrel Bank",
        instructions="Card operations for Kestrel Bank Customers. Call verify_identity first.",
    )

    def session_of(ctx: Context[Any, Any]) -> VerifiedSession:
        meta = ctx.request_context.meta
        token = meta.get(SESSION_META_KEY) if meta else None
        if not isinstance(token, str):
            raise IdentityNotVerified()
        return tokens.read(token)

    async def issued(session: Awaitable[VerifiedSession]) -> CallToolResult:
        return _result(Verified(), meta={SESSION_META_KEY: tokens.issue(await session)})

    @server.tool()
    async def verify_identity(
        customer_number: Annotated[str, Field(description="The Customer's customer number.")],
        date_of_birth: Annotated[date, Field(description="The Customer's date of birth.")],
        postcode: Annotated[str, Field(description="The postcode of the Customer's address.")],
    ) -> CallToolResult:
        """Verify that the person in the conversation is the Customer.

        Required before any other tool. Ask the person for all three details; never guess them.
        """
        claim = IdentityClaim(CustomerId(customer_number), date_of_birth, postcode)
        return await _answered(lambda: issued(bank.verify_identity(claim)))

    @server.tool()
    async def list_cards(ctx: Context[Any, Any]) -> CallToolResult:
        """List the verified Customer's cards with their ids and whether each is frozen."""
        return await _answered(lambda: _cards(bank.list_cards(session_of(ctx))))

    @server.tool()
    async def freeze_card(card_id: CardIdArg, ctx: Context[Any, Any]) -> CallToolResult:
        """Temporarily freeze one of the verified Customer's cards; unfreeze_card reverses it."""
        return await _answered(lambda: _card(bank.freeze_card(session_of(ctx), CardId(card_id))))

    @server.tool()
    async def unfreeze_card(card_id: CardIdArg, ctx: Context[Any, Any]) -> CallToolResult:
        """Lift a freeze the Customer placed on one of their cards."""
        return await _answered(lambda: _card(bank.unfreeze_card(session_of(ctx), CardId(card_id))))

    return server


async def _answered(operation: Callable[[], Awaitable[CallToolResult]]) -> CallToolResult:
    """The one place Hard Rule refusals become structured tool results (ADR-0017)."""
    try:
        return await operation()
    except HardRuleRefusal as refusal:
        return _result(Refusal(rule=type(refusal).__name__, message=str(refusal)), is_error=True)


async def _card(card: Awaitable[Card]) -> CallToolResult:
    return _result(CardView.of(await card))


async def _cards(cards: Awaitable[list[Card]]) -> CallToolResult:
    return _result(CardList(cards=[CardView.of(card) for card in await cards]))


def _result(
    body: BaseModel, *, is_error: bool = False, meta: dict[str, Any] | None = None
) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=body.model_dump_json())],
        structured_content=body.model_dump(mode="json"),
        is_error=is_error,
        _meta=meta,
    )


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode())
