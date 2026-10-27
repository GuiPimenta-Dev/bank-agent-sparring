"""The Golden Set: the regression suite that grows only through Promotion (ADR-0005)."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from langfuse import Langfuse
from langfuse.api import DatasetStatus, NotFoundError

from sparring.evaluation.scenario import (
    BankSnapshot,
    Scenario,
    scenario_from_json,
    scenario_to_json,
    snapshot_to_json,
)


@dataclass(frozen=True)
class GoldenItem:
    scenario: Scenario
    signature: str
    """The Failure Signature that brought the Scenario in."""
    source_trace_id: str | None
    """The conversation whose Failure it was."""


class GoldenSet(Protocol):
    """Append-only: Scenarios are added, never edited or deleted (ADR-0005)."""

    async def items(self) -> list[GoldenItem]:
        """Every Scenario in the Golden Set that has not been archived."""
        ...

    async def add(self, item: GoldenItem, expected: BankSnapshot) -> None: ...


class InMemoryGoldenSet:
    def __init__(self) -> None:
        self.added: list[tuple[GoldenItem, BankSnapshot]] = []

    async def items(self) -> list[GoldenItem]:
        return [item for item, _ in self.added]

    async def add(self, item: GoldenItem, expected: BankSnapshot) -> None:
        self.added.append((item, expected))


class LangfuseGoldenSet:
    """A Langfuse Dataset. Each item's input is the whole Scenario and its expected output
    the Expected Outcome, so an item stands on its own; items are only ever created."""

    def __init__(self, langfuse: Langfuse, name: str = "golden-set") -> None:
        self._langfuse = langfuse
        self._name = name

    async def items(self) -> list[GoldenItem]:
        return await asyncio.to_thread(self._items)

    async def add(self, item: GoldenItem, expected: BankSnapshot) -> None:
        await asyncio.to_thread(self._add, item, expected)

    def _items(self) -> list[GoldenItem]:
        try:
            dataset = self._langfuse.get_dataset(self._name)
        except NotFoundError:
            return []
        return [
            GoldenItem(
                scenario_from_json(item.input),
                item.metadata["failure_signature"],
                item.source_trace_id,
            )
            for item in dataset.items
            if item.status == DatasetStatus.ACTIVE
        ]

    def _add(self, item: GoldenItem, expected: BankSnapshot) -> None:
        self._langfuse.create_dataset(
            name=self._name, description="Scenarios promoted from Failures (ADR-0005)."
        )
        self._langfuse.create_dataset_item(
            dataset_name=self._name,
            input=scenario_to_json(item.scenario),
            expected_output=snapshot_to_json(expected),
            metadata={"failure_signature": item.signature, "scenario_id": item.scenario.id},
            source_trace_id=item.source_trace_id,
        )
