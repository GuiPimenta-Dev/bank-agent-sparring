import json
from typing import Any

import httpx2
import openai
import pytest

from sparring.evaluation.cost import Usage
from sparring.evaluation.judges import (
    DriftJudgement,
    OpenAIPolicyDriftJudge,
    OpenAIToneJudge,
    ToneJudgement,
    as_text,
)
from sparring.evaluation.simulated_user import ToolCall, Turn

pytestmark = pytest.mark.anyio

TRANSCRIPT = [
    Turn("customer", "Unfreeze my credit card."),
    Turn(
        "agent",
        "Only our fraud team can lift that freeze.",
        (ToolCall("unfreeze_card", {"card_id": "card-ana-credit"}, "FraudFreezeLocked"),),
    ),
    Turn("customer_channel", "You confirmed request pa-1."),
]


def test_a_judge_reads_the_agent_s_tool_calls_and_refusals() -> None:
    assert as_text(TRANSCRIPT).splitlines() == [
        "Customer: Unfreeze my credit card.",
        "  (tool) unfreeze_card({'card_id': 'card-ana-credit'}) -> refused: FraudFreezeLocked",
        "Agent: Only our fraud team can lift that freeze.",
        "[Customer's app] You confirmed request pa-1.",
    ]


def answering(answer: dict[str, Any], requests: list[dict[str, Any]]) -> openai.AsyncOpenAI:
    """An OpenAI client whose provider answers every request with `answer`."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        message: dict[str, Any] = {
            "type": "message",
            "id": "msg-1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": json.dumps(answer), "annotations": []}],
        }
        usage = {
            "input_tokens": 700,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 80,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 780,
        }
        body: dict[str, Any] = {
            "id": "resp-1",
            "object": "response",
            "created_at": 0,
            "model": "pinned-simulator-2026-10-01",
            "status": "completed",
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "output": [message],
            "usage": usage,
        }
        return httpx2.Response(200, json=body)

    transport = httpx2.MockTransport(handler)
    return openai.AsyncOpenAI(
        api_key="test-key", http_client=httpx2.AsyncClient(transport=transport)
    )


async def test_the_drift_judge_asks_the_pinned_model_with_the_policy_and_the_findings() -> None:
    requests: list[dict[str, Any]] = []
    client = answering({"reasoning": "FR-4 says to Escalate.", "justified": False}, requests)
    judge = OpenAIPolicyDriftJudge(client, "pinned-simulator", "FR-4 (Judgment) Escalate.")

    judgement = await judge.judge(TRANSCRIPT, ["escalated: expected True, found False"])

    assert judgement == DriftJudgement(
        False,
        "FR-4 says to Escalate.",
        Usage("pinned-simulator", input_tokens=700, output_tokens=80),
    )
    [request] = requests
    assert request["model"] == "pinned-simulator"
    assert "FR-4 (Judgment) Escalate." in request["instructions"]
    assert "escalated: expected True, found False" in request["input"]


async def test_the_tone_judge_scores_the_agent_on_the_pinned_model() -> None:
    requests: list[dict[str, Any]] = []
    client = answering({"reasoning": "Clear and kind.", "score": 4}, requests)

    judgement = await OpenAIToneJudge(client, "pinned-simulator").judge(TRANSCRIPT)

    assert judgement.score == 4 and judgement.reasoning == "Clear and kind."
    assert isinstance(judgement, ToneJudgement)
    assert requests[0]["model"] == "pinned-simulator"
