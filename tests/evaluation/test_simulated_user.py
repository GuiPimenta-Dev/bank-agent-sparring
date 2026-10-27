import httpx2
import openai
import pytest

from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.simulated_user import OpenAISimulatedUserModel

pytestmark = pytest.mark.anyio


def provider_answering(status: int, requests: list[httpx2.Request]) -> OpenAISimulatedUserModel:
    """The Simulated User's model on a provider that answers every request with `status`."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            status, headers={"retry-after-ms": "1"}, json={"error": {"message": "unavailable"}}
        )

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
