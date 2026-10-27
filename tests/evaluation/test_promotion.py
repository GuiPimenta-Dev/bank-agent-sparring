import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from sparring.evaluation.conversation import Conversation, Verdict
from sparring.evaluation.cost import Usage
from sparring.evaluation.golden_set import GoldenItem, InMemoryGoldenSet
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.judges import DriftJudgement
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.promotion import PER_SIGNATURE, REPRODUCTIONS, Decision, Promotion
from sparring.evaluation.quarantine import InMemoryQuarantine
from sparring.evaluation.recording import InMemoryRecorder
from sparring.evaluation.scenario import Scenario, load_scenario
from sparring.evaluation.simulated_user import Turn

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


JUDGE_USAGE = Usage("simulator", input_tokens=5_000, output_tokens=200)


class DriftJudge:
    """Always gives the same view, or is unreachable when `suspected` is None."""

    def __init__(self, suspected: bool | None = False) -> None:
        self._suspected = suspected
        self.asked: list[Sequence[str]] = []

    async def judge(self, transcript: Sequence[Turn], findings: Sequence[str]) -> DriftJudgement:
        self.asked.append(findings)
        if self._suspected is None:
            raise InfrastructureFailure("OpenAI is unreachable")
        return DriftJudgement(self._suspected, "The Policy text is silent on this.", JUDGE_USAGE)


class Gate:
    """A Promotion over in-memory adapters, and everything it touched."""

    def __init__(self, judge: DriftJudge | None = None) -> None:
        self.golden_set = InMemoryGoldenSet()
        self.recorder = InMemoryRecorder()
        self.quarantine = InMemoryQuarantine()
        self.judge = judge or DriftJudge()
        self.usage: list[Usage] = []
        self.promotion = Promotion(
            self.golden_set, Oracle(), self.judge, self.quarantine, self.recorder
        )

    async def consider(
        self, conversation: Conversation, verdict: Verdict, reruns: "Reruns"
    ) -> Decision | None:
        return await self.promotion.consider(conversation, verdict, reruns, self.usage)


def promotion() -> tuple[Gate, InMemoryGoldenSet, InMemoryRecorder]:
    gate = Gate()
    return gate, gate.golden_set, gate.recorder


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


async def test_a_failure_the_policy_text_seems_to_justify_is_quarantined_not_promoted() -> None:
    gate = Gate(DriftJudge(suspected=True))

    assert await gate.consider(failure(), FAILED, Reruns(FAILED)) == "quarantined"

    assert gate.golden_set.added == []
    assert gate.quarantine.held == ["trace-1"]
    assert gate.recorder.drift["trace-1"].suspected
    assert gate.recorder.promotions == {"trace-1": "quarantined"}


async def test_the_drift_judge_sees_the_findings_and_its_cost_is_counted() -> None:
    gate = Gate()

    assert await gate.consider(failure(), FAILED, Reruns(FAILED)) == "promoted"

    assert gate.judge.asked == [FAILED.differences]
    assert not gate.recorder.drift["trace-1"].suspected
    assert gate.usage == [JUDGE_USAGE]


async def test_an_unreachable_drift_judge_blocks_the_promotion() -> None:
    gate = Gate(DriftJudge(suspected=None))

    assert await gate.consider(failure(), FAILED, Reruns(FAILED)) == "judge_unavailable"

    assert gate.golden_set.added == [] and gate.quarantine.held == []


async def test_no_judge_result_can_cause_a_promotion() -> None:
    """Whatever the judge says, only a reproduced deterministic Failure is promoted."""
    inconclusive = Verdict(True, [], [])
    for suspected in (True, False):
        for verdict, reruns in [
            (PASSED, Reruns(FAILED)),
            (inconclusive, Reruns(FAILED)),
            (FAILED, Reruns(*[PASSED] * REPRODUCTIONS)),
            (FAILED, Reruns(None)),
        ]:
            gate = Gate(DriftJudge(suspected))

            await gate.consider(failure(), verdict, reruns)

            assert gate.golden_set.added == [], (suspected, verdict, reruns)
        suspicious = Gate(DriftJudge(suspected))
        await suspicious.consider(failure(), FAILED, Reruns(FAILED))
        assert bool(suspicious.golden_set.added) is not suspected
