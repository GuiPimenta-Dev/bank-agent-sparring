"""Adaptive Sampling: Exploration chooses Dimension values in proportion to their observed
Failure rate, with a floor so that no value is starved.

The rates come from the raw `failure` scores of a recent time window, aggregated in Python
(ADR-0012). Each rate is smoothed (Laplace), so a value seen once is not judged on one
conversation, and a value never seen starts at one half.
"""

import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from langfuse import Langfuse

FLOOR = 0.5
"""The share of its uniform probability that every value keeps, whatever its Failure rate."""

Weights = Mapping[str, Mapping[str, float]]
"""For each Dimension, the probability of each of its values."""


@dataclass(frozen=True)
class Observation:
    """One conclusive conversation: its Scenario's Dimension values and whether it failed."""

    dimensions: Mapping[str, str]
    failed: bool


def weights(
    observations: Sequence[Observation],
    choices: Mapping[str, Sequence[str]],
    floor: float = FLOOR,
) -> Weights:
    """Every value of a Dimension gets at least `floor / len(values)`; the rest is shared in
    proportion to the values' smoothed Failure rates."""
    seen: Counter[tuple[str, str]] = Counter()
    failed: Counter[tuple[str, str]] = Counter()
    for observation in observations:
        for name, value in observation.dimensions.items():
            seen[name, value] += 1
            failed[name, value] += observation.failed
    result: dict[str, dict[str, float]] = {}
    for name, values in choices.items():
        rates = {v: (failed[name, v] + 1) / (seen[name, v] + 2) for v in values}
        total = sum(rates.values())
        result[name] = {v: floor / len(values) + (1 - floor) * rates[v] / total for v in values}
    return result


class ObservationLog(Protocol):
    async def observations(self, since: datetime) -> list[Observation]:
        """Every conclusive conversation recorded since `since`."""
        ...


class InMemoryObservationLog:
    def __init__(self, observations: Sequence[Observation] = ()) -> None:
        self._observations = list(observations)

    async def observations(self, since: datetime) -> list[Observation]:
        return list(self._observations)


class LangfuseObservationLog:
    """Reads the raw `failure` scores, which carry their Scenario's Dimension values as
    metadata because scores do not carry their trace's."""

    def __init__(self, langfuse: Langfuse) -> None:
        self._langfuse = langfuse

    async def observations(self, since: datetime) -> list[Observation]:
        return await asyncio.to_thread(self._observations, since)

    def _observations(self, since: datetime) -> list[Observation]:
        observations: list[Observation] = []
        cursor: str | None = None
        while True:
            page = self._langfuse.api.scores.get_many(
                name="failure",
                from_timestamp=since,
                fields="core,details",
                limit=100,
                cursor=cursor,
            )
            observations += [
                Observation(
                    {str(k): str(v) for k, v in (score.metadata or {}).items()},
                    bool(score.value),
                )
                for score in page.data
            ]
            cursor = page.meta.cursor
            if not page.data or cursor is None:
                return observations
