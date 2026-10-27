"""Quarantine: Failures suspected of Policy Drift, held for a human decision instead of
Promotion (ADR-0002)."""

import asyncio
import threading
from typing import Protocol

from langfuse import Langfuse
from langfuse.api import AnnotationQueueObjectType, ConfigCategory, ScoreConfigDataType

QUEUE = "quarantine"
DECISION = "quarantine_decision"
DECISIONS = (
    "agent_wrong",
    "policy_text_unclear",
    "oracle_wrong",
)


class Quarantine(Protocol):
    async def hold(self, trace_id: str) -> None:
        """Holds the conversation recorded under `trace_id` for a human decision."""
        ...


class InMemoryQuarantine:
    def __init__(self) -> None:
        self.held: list[str] = []

    async def hold(self, trace_id: str) -> None:
        self.held.append(trace_id)


class LangfuseQuarantine:
    """A Langfuse annotation queue. The human records their decision on the trace with the
    `quarantine_decision` score: the agent was wrong after all, the Policy text is unclear,
    or the Oracle is wrong."""

    def __init__(self, langfuse: Langfuse, queue: str = QUEUE) -> None:
        self._langfuse = langfuse
        self._name = queue
        self._queue_id: str | None = None
        # Holds run in worker threads; only one may create the queue.
        self._creating = threading.Lock()

    async def hold(self, trace_id: str) -> None:
        await asyncio.to_thread(self._hold, trace_id)

    def _hold(self, trace_id: str) -> None:
        self._langfuse.api.annotation_queues.create_queue_item(
            queue_id=self._queue(),
            object_id=trace_id,
            object_type=AnnotationQueueObjectType.TRACE,
        )

    def _queue(self) -> str:
        with self._creating:
            return self._queue_id or self._find_or_create_queue()

    def _find_or_create_queue(self) -> str:
        if self._queue_id is None:
            api = self._langfuse.api
            queues = {q.name: q.id for q in api.annotation_queues.list_queues(limit=100).data}
            self._queue_id = queues.get(self._name) or (
                api.annotation_queues.create_queue(
                    name=self._name,
                    score_config_ids=[self._decision_config()],
                    description="Failures suspected of Policy Drift (ADR-0002).",
                ).id
            )
        return self._queue_id

    def _decision_config(self) -> str:
        configs = self._langfuse.api.score_configs.get(limit=100).data
        existing = next((config.id for config in configs if config.name == DECISION), None)
        return existing or (
            self._langfuse.api.score_configs.create(
                name=DECISION,
                data_type=ScoreConfigDataType.CATEGORICAL,
                categories=[
                    ConfigCategory(value=index, label=label)
                    for index, label in enumerate(DECISIONS)
                ],
                description="What a human decided about a quarantined Failure.",
            ).id
        )
