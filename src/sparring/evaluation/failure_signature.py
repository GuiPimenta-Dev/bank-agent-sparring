"""Failure Signatures: deterministic labels for what went wrong, so that Failures sharing
one count as the same problem however their Scenarios differ.

A signature keeps the operation, which fields went wrong and how, and which Invariants were
violated. It leaves out ids and amounts, which vary from one Scenario to the next.
"""

import asyncio
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from langfuse import Langfuse

from sparring.evaluation.golden_set import GoldenItem
from sparring.evaluation.invariants import InvariantViolation
from sparring.evaluation.scenario import Difference, Operation


def failure_signature(
    operation: Operation,
    differences: Sequence[Difference],
    violations: Sequence[InvariantViolation],
) -> str:
    parts: list[str] = []
    if differences:
        parts.append(f"{operation}: {', '.join(sorted({_part(d) for d in differences}))}")
    parts += sorted({f"invariant: {violation.invariant}" for violation in violations})
    return " | ".join(parts)


def _part(difference: Difference) -> str:
    if difference.kind == "customers":
        return "customers"
    if difference.field is None and difference.kind != "escalated":
        return f"{difference.kind} {'missing' if difference.found is None else 'unexpected'}"
    name = ".".join(part for part in (difference.kind, difference.field) if part)
    return f"{name} {_shape(difference.expected)}->{_shape(difference.found)}"


def _shape(value: Any) -> str:
    """What a value says about the problem, without what varies between Scenarios."""
    match value:
        case None:
            return "none"
        case bool():
            return str(value).lower()
        case Enum():
            return str(value.value)
        case _:
            return "set"


@dataclass(frozen=True)
class SignatureCount:
    signature: str
    failures: int
    in_golden_set: int


def signature_counts(
    signatures: Iterable[str], golden_set: Iterable[GoldenItem]
) -> list[SignatureCount]:
    """Every Failure Signature seen, most frequent first, with how many of its Scenarios
    the Golden Set holds."""
    seen = Counter(signatures)
    promoted = Counter(item.signature for item in golden_set)
    counts = [SignatureCount(s, n, promoted[s]) for s, n in seen.items()]
    return sorted(counts, key=lambda count: (-count.failures, count.signature))


class FailureLog(Protocol):
    """The Failure Signatures recorded since a moment, one per Failure (ADR-0012)."""

    async def signatures(self, since: datetime) -> list[str]: ...


class LangfuseFailureLog:
    """Reads the raw `failure_signature` scores and leaves the counting to Python, since
    Langfuse's metrics cannot group by them (ADR-0012)."""

    def __init__(self, langfuse: Langfuse) -> None:
        self._langfuse = langfuse

    async def signatures(self, since: datetime) -> list[str]:
        return await asyncio.to_thread(self._signatures, since)

    def _signatures(self, since: datetime) -> list[str]:
        signatures: list[str] = []
        cursor: str | None = None
        while True:
            page = self._langfuse.api.scores.get_many(
                name="failure_signature", from_timestamp=since, limit=100, cursor=cursor
            )
            signatures += [str(score.value) for score in page.data]
            cursor = page.meta.cursor
            if not page.data or cursor is None:
                return signatures
