"""The Customer Channel over HTTP: the Kestrel Bank app, where a Customer sees what awaits
their Confirmation and confirms or rejects it (ADR-0004). The agent never reaches it."""

from collections.abc import Mapping

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from sparring.bank.application.bank import Bank
from sparring.bank.domain.customer import CustomerId
from sparring.bank.domain.pending_action import PendingActionId
from sparring.bank.domain.refusals import HardRuleRefusal


class AppSessions:
    """Which Customer each app session token belongs to. How the app logs a Customer in is
    out of scope; the demo has one token, for its one Customer."""

    def __init__(self, tokens: Mapping[str, CustomerId]) -> None:
        self._tokens = tokens

    def customer(self, request: Request) -> CustomerId | None:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        return self._tokens.get(token) if scheme.lower() == "bearer" else None


def customer_channel_routes(bank: Bank, sessions: AppSessions) -> list[Route]:
    async def awaiting(request: Request) -> Response:
        customer = sessions.customer(request)
        if customer is None:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        actions = await bank.awaiting_confirmation(customer)
        return JSONResponse(
            {
                "pending_actions": [
                    {"id": a.id, "summary": a.summary, "expires_at": a.expires_at.isoformat()}
                    for a in actions
                ]
            }
        )

    async def answer(request: Request) -> Response:
        customer = sessions.customer(request)
        if customer is None:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        action_id = PendingActionId(request.path_params["action_id"])
        match request.path_params["answer"]:
            case "confirm":
                answer = bank.confirm
            case "reject":
                answer = bank.reject
            case _:
                return JSONResponse({"error": "answer with confirm or reject"}, status_code=404)
        try:
            await answer(customer, action_id)
        except HardRuleRefusal as refusal:
            return JSONResponse(
                {"rule": type(refusal).__name__, "message": str(refusal)}, status_code=409
            )
        return Response(status_code=204)

    return [
        Route("/channel/pending-actions", awaiting, methods=["GET"]),
        Route("/channel/pending-actions/{action_id}/{answer}", answer, methods=["POST"]),
    ]
