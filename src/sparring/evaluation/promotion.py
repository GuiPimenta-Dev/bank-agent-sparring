"""Promotion: how a Failure enters the Golden Set, with no human in the loop.

Every Failure already rests on deterministic evidence, an Oracle difference or an
Invariant violation (ADR-0002). Before it is promoted, its Scenario is Reproduced with
fresh Simulated User runs, so a Failure the simulator alone caused stays out. Then the
Policy Drift judge may block it: a Failure the Policy text seems to justify goes to
Quarantine instead. The judge can only block; nothing it says can promote. Each Failure
Signature brings in at most a few Scenarios; further occurrences only add to its count.
"""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Literal

from sparring.evaluation.conversation import Conversation, Verdict
from sparring.evaluation.cost import Usage
from sparring.evaluation.golden_set import GoldenItem, GoldenSet
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.judges import PolicyDriftJudge
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.quarantine import Quarantine
from sparring.evaluation.recording import Recorder
from sparring.evaluation.scenario import Scenario

REPRODUCTIONS = 3
PER_SIGNATURE = 3

Decision = Literal[
    "promoted",
    "not_reproduced",
    "quarantined",
    "signature_full",
    "unaffordable",
    "judge_unavailable",
]

Rerun = Callable[[Scenario], Awaitable[Verdict | None]]
"""Plays the Scenario again with a fresh Simulated User and agent; None when the run's
budget cannot cover another conversation."""


class Promotion:
    def __init__(
        self,
        golden_set: GoldenSet,
        oracle: Oracle,
        drift_judge: PolicyDriftJudge,
        quarantine: Quarantine,
        recorder: Recorder,
    ) -> None:
        self._golden_set = golden_set
        self._oracle = oracle
        self._drift_judge = drift_judge
        self._quarantine = quarantine
        self._recorder = recorder
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def consider(
        self, conversation: Conversation, verdict: Verdict, rerun: Rerun, usage: list[Usage]
    ) -> Decision | None:
        """Decides about a Failure and records the decision on its trace; None for anything
        that is not a Failure. What the drift judge consumes is added to `usage`."""
        if verdict.signature is None:
            return None
        # One decision per Signature at a time, so concurrent Failures cannot both slip
        # under the cap.
        async with self._locks[verdict.signature]:
            decision = await self._decide(conversation, verdict, verdict.signature, rerun, usage)
        self._recorder.promotion_decided(conversation.trace_id, decision)
        return decision

    async def _decide(
        self,
        conversation: Conversation,
        verdict: Verdict,
        signature: str,
        rerun: Rerun,
        usage: list[Usage],
    ) -> Decision:
        promoted = [item for item in await self._golden_set.items() if item.signature == signature]
        if len(promoted) >= PER_SIGNATURE:
            return "signature_full"
        for _ in range(REPRODUCTIONS):
            reproduction = await rerun(conversation.scenario)
            if reproduction is None:
                return "unaffordable"
            if reproduction.failed:
                break
        else:
            return "not_reproduced"
        findings = [
            *verdict.differences,
            *(f"{v.invariant}: {v.detail}" for v in verdict.violations),
        ]
        try:
            judgement = await self._drift_judge.judge(conversation.transcript, findings)
        except InfrastructureFailure:
            return "judge_unavailable"
        usage += [judgement.usage] if judgement.usage else []
        self._recorder.drift_judged(conversation.trace_id, judgement)
        if judgement.suspected:
            if conversation.trace_id is not None:
                await self._quarantine.hold(conversation.trace_id)
            return "quarantined"
        item = GoldenItem(conversation.scenario, signature, conversation.trace_id)
        await self._golden_set.add(item, self._oracle.expected_outcome(item.scenario))
        return "promoted"
