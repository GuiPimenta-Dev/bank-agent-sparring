from typing import Any

import httpx2
import openai
import pytest

from sparring.evaluation.cost import Usage
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.simulated_user import OpenAISimulatedUserModel, UserMove

pytestmark = pytest.mark.anyio


def provider_answering(
    status: int, requests: list[httpx2.Request], body: object = None
) -> OpenAISimulatedUserModel:
    """The Simulated User's model on a provider that answers every request with `status`."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        error = {"error": {"message": "unavailable"}}
        return httpx2.Response(status, headers={"retry-after-ms": "1"}, json=body or error)

    client = openai.AsyncOpenAI(
        api_key="test-key",
        max_retries=2,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return OpenAISimulatedUserModel(client, "gpt-test")


async def test_a_provider_still_failing_after_retries_is_an_infrastructure_failure() -> None:
    requests: list[httpx2.Request] = []

    with pytest.raises(InfrastructureFailure):
        await provider_answering(503, requests).next_move("Play Ana.", [])

    assert len(requests) == 3, "the first attempt and both retries"


async def test_a_request_the_provider_rejects_is_not_an_outage() -> None:
    requests: list[httpx2.Request] = []

    with pytest.raises(openai.BadRequestError):
        await provider_answering(400, requests).next_move("Play Ana.", [])

    assert len(requests) == 1


MOVE = '{"message": "Freeze my card, please.", "ends_conversation": false, "decisions": []}'
RESPONSE: dict[str, Any] = {
    "id": "resp-1",
    "object": "response",
    "created_at": 0,
    "model": "gpt-test-2026-10-01",
    "status": "completed",
    "parallel_tool_calls": False,
    "tool_choice": "auto",
    "tools": [],
    "output": [
        {
            "type": "message",
            "id": "msg-1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": MOVE, "annotations": []}],
        }
    ],
    "usage": {
        "input_tokens": 1_200,
        "input_tokens_details": {"cached_tokens": 1_000},
        "output_tokens": 300,
        "output_tokens_details": {"reasoning_tokens": 200},
        "total_tokens": 1_500,
    },
}


async def test_a_move_carries_its_usage_priced_as_the_pinned_model() -> None:
    move = await provider_answering(200, [], RESPONSE).next_move("Play Ana.", [])

    assert move == UserMove(
        "Freeze my card, please.",
        ends_conversation=False,
        usage=Usage("gpt-test", input_tokens=200, output_tokens=300, cache_read_tokens=1_000),
    )
