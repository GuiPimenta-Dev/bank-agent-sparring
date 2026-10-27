import asyncio
from pathlib import Path

import pytest

from sparring.evaluation.conversation import Conversation, Verdict
from sparring.evaluation.golden_set import GoldenItem, InMemoryGoldenSet
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.promotion import PER_SIGNATURE, REPRODUCTIONS, Promotion
from sparring.evaluation.recording import InMemoryRecorder
from sparring.evaluation.scenario import Scenario, load_scenario

pytestmark = pytest.mark.anyio

CASES = Path(__file__).parents[2] / "canonical_cases"
SIGNATURE = "freeze_card: card.frozen_by customer->none"
FAILED = Verdict(False, ["card card-ana-debit: frozen_by ..."], [], SIGNATURE)
PASSED = Verdict(False, [], [])


def failure(trace_id: str = "trace-1") -> Conversation:
    scenario = load_scenario(CASES / "freeze-active-debit-card.json")
    return Conversation("conversation-1", scenario, [], scenario.bank, False, trace_id=trace_id)


class Reruns:
    """Reproductions that end with the given verdicts, in order."""

    def __init__(self, *verdicts: Verdict | None) -> None:
        self._verdicts = iter(verdicts)
        self.played = 0

    async def __call__(self, scenario: Scenario) -> Verdict | None:
        self.played += 1
        await asyncio.sleep(0)  # a real conversation lets others run meanwhile
        return next(self._verdicts)


def promotion() -> tuple[Promotion, InMemoryGoldenSet, InMemoryRecorder]:
    golden_set, recorder = InMemoryGoldenSet(), InMemoryRecorder()
    return Promotion(golden_set, Oracle(), recorder), golden_set, recorder


async def test_a_failure_that_reproduces_is_promoted_as_a_self_contained_scenario() -> None:
    gate, golden_set, recorder = promotion()
    conversation = failure()
    reruns = Reruns(PASSED, FAILED)

    assert await gate.consider(conversation, FAILED, reruns) == "promoted"

    [(item, expected)] = golden_set.added
    assert item == GoldenItem(conversation.scenario, SIGNATURE, "trace-1")
    assert expected == Oracle().expected_outcome(conversation.scenario)
    assert reruns.played == 2, "the first failing Reproduction is enough"
    assert recorder.promotions == {"trace-1": "promoted"}


async def test_a_failure_that_does_not_reproduce_is_tagged_and_left_out() -> None:
    gate, golden_set, recorder = promotion()
    reruns = Reruns(*[PASSED] * REPRODUCTIONS)

    assert await gate.consider(failure(), FAILED, reruns) == "not_reproduced"

    assert reruns.played == REPRODUCTIONS
    assert golden_set.added == []
    assert recorder.promotions == {"trace-1": "not_reproduced"}


async def test_a_full_signature_only_adds_to_its_count() -> None:
    gate, golden_set, recorder = promotion()
    for n in range(PER_SIGNATURE):
        await golden_set.add(
            GoldenItem(failure().scenario, SIGNATURE, f"old-{n}"), failure().scenario.bank
        )
    reruns = Reruns()

    assert await gate.consider(failure(), FAILED, reruns) == "signature_full"

    assert reruns.played == 0, "no money is spent reproducing it"
    assert len(golden_set.added) == PER_SIGNATURE
    assert recorder.promotions == {"trace-1": "signature_full"}


async def test_other_signatures_do_not_count_toward_the_cap() -> None:
    gate, golden_set, _ = promotion()
    for n in range(PER_SIGNATURE):
        await golden_set.add(
            GoldenItem(failure().scenario, "invariant: tool_call_cap", f"old-{n}"),
            failure().scenario.bank,
        )

    assert await gate.consider(failure(), FAILED, Reruns(FAILED)) == "promoted"


async def test_a_reproduction_the_budget_cannot_cover_promotes_nothing() -> None:
    gate, golden_set, recorder = promotion()

    assert await gate.consider(failure(), FAILED, Reruns(PASSED, None)) == "unaffordable"

    assert golden_set.added == []
    assert recorder.promotions == {"trace-1": "unaffordable"}


async def test_a_conversation_that_did_not_fail_is_not_considered() -> None:
    gate, golden_set, recorder = promotion()
    inconclusive = Verdict(True, [], [])

    assert await gate.consider(failure(), PASSED, Reruns()) is None
    assert await gate.consider(failure(), inconclusive, Reruns()) is None
    assert golden_set.added == [] and recorder.promotions == {}


async def test_concurrent_failures_with_one_signature_respect_the_cap() -> None:
    gate, golden_set, _ = promotion()
    for n in range(PER_SIGNATURE - 1):
        await golden_set.add(
            GoldenItem(failure().scenario, SIGNATURE, f"old-{n}"), failure().scenario.bank
        )

    decisions = await asyncio.gather(
        *(gate.consider(failure(f"trace-{n}"), FAILED, Reruns(FAILED)) for n in range(3))
    )

    assert sorted(d or "" for d in decisions) == ["promoted", "signature_full", "signature_full"]
    assert len(golden_set.added) == PER_SIGNATURE
