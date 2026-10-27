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
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, Field

from sparring.bank.application.bank import Bank
from sparring.bank.application.ports import Clock
from sparring.bank.domain.card import Card, CardId, LossReason
from sparring.bank.domain.customer import CustomerId, IdentityClaim, Tier
from sparring.bank.domain.pending_action import PendingAction, PendingActionId, PendingActionStatus
from sparring.bank.domain.refusals import HardRuleRefusal, IdentityNotVerified
from sparring.bank.domain.transaction import Transaction, TransactionId
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
    status: Literal["active", "frozen", "cancelled"]
    frozen_by: str | None
    replaces: str | None

    @classmethod
    def of(cls, card: Card) -> "CardView":
        status = "cancelled" if card.cancelled_because else "frozen" if card.frozen_by else "active"
        return cls(
            card_id=card.id,
            last_four=card.last_four,
            status=status,
            frozen_by=card.frozen_by,
            replaces=card.replaces,
        )


class TransactionView(BaseModel):
    transaction_id: str
    card_id: str
    merchant: str
    amount: Decimal
    currency: str
    status: Literal["pending", "settled"]
    settled_on: date | None

    @classmethod
    def of(cls, transaction: Transaction) -> "TransactionView":
        return cls(
            transaction_id=transaction.id,
            card_id=transaction.card_id,
            merchant=transaction.merchant,
            amount=transaction.amount.amount,
            currency=transaction.amount.currency,
            status="settled" if transaction.settled_on else "pending",
            settled_on=transaction.settled_on,
        )


class PendingActionView(BaseModel):
    pending_action_id: str
    status: Literal["awaiting_confirmation", "confirmed", "rejected", "expired"]
    summary: str
    expires_at: datetime
    next_step: str

    @classmethod
    def of(cls, action: PendingAction, now: datetime) -> "PendingActionView":
        closed = "Nothing; this request is closed."
        match action.status:
            case PendingActionStatus.CONFIRMED:
                status, next_step = "confirmed", closed
            case PendingActionStatus.REJECTED:
                status, next_step = "rejected", closed
            case PendingActionStatus.AWAITING if action.is_awaiting(now):
                status = "awaiting_confirmation"
                next_step = "The Customer must confirm or reject it in the Kestrel Bank app."
            case PendingActionStatus.AWAITING:
                status, next_step = "expired", closed
        return cls(
            pending_action_id=action.id,
            status=status,
            summary=action.summary,
            expires_at=action.expires_at,
            next_step=next_step,
        )


class CardList(BaseModel):
    cards: list[CardView]


class TransactionList(BaseModel):
    transactions: list[TransactionView]


class Refusal(BaseModel):
    refused: Literal[True] = True
    rule: str
    message: str


class Verified(BaseModel):
    verified: Literal[True] = True
    tier: Tier


def build_mcp_server(bank: Bank, tokens: SessionTokens, clock: Clock) -> MCPServer:
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

    async def issued(verification: Awaitable[VerifiedSession]) -> CallToolResult:
        session = await verification
        customer = await bank.customer(session)
        return _result(Verified(tier=customer.tier), meta={SESSION_META_KEY: tokens.issue(session)})

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

    @server.tool()
    async def report_lost_or_stolen(
        card_id: CardIdArg,
        reason: Annotated[LossReason, Field(description="Whether the card was lost or stolen.")],
        ctx: Context[Any, Any],
    ) -> CallToolResult:
        """Request the permanent cancellation of a lost or stolen card and a Replacement Card.

        Nothing happens until the Customer confirms it in the Kestrel Bank app; you cannot
        confirm it for them. Use get_pending_action to see whether they have.
        """
        return await _answered(
            lambda: action(
                bank.request_lost_or_stolen_report(session_of(ctx), CardId(card_id), reason)
            )
        )

    @server.tool()
    async def list_transactions(ctx: Context[Any, Any]) -> CallToolResult:
        """List the verified Customer's card transactions, pending or settled."""
        return await _answered(lambda: _transactions(bank.list_transactions(session_of(ctx))))

    @server.tool()
    async def dispute_transaction(
        transaction_id: Annotated[
            str, Field(description="The transaction's id, as returned by list_transactions.")
        ],
        ctx: Context[Any, Any],
    ) -> CallToolResult:
        """Request a Dispute of a settled transaction the Customer says is wrong or did not
        authorize, with Provisional Credit if the Policy grants it.

        Nothing happens until the Customer confirms it in the Kestrel Bank app; you cannot
        confirm it for them. Use get_pending_action to see whether they have.
        """
        return await _answered(
            lambda: action(bank.request_dispute(session_of(ctx), TransactionId(transaction_id)))
        )

    @server.tool()
    async def get_pending_action(
        pending_action_id: Annotated[str, Field(description="As returned when it was requested.")],
        ctx: Context[Any, Any],
    ) -> CallToolResult:
        """Whether the Customer has confirmed, rejected or let expire a pending request."""
        return await _answered(
            lambda: action(bank.pending_action(session_of(ctx), PendingActionId(pending_action_id)))
        )

    async def action(pending: Awaitable[PendingAction]) -> CallToolResult:
        return _result(PendingActionView.of(await pending, clock.now()))

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


async def _transactions(transactions: Awaitable[list[Transaction]]) -> CallToolResult:
    views = [TransactionView.of(transaction) for transaction in await transactions]
    return _result(TransactionList(transactions=views))


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
