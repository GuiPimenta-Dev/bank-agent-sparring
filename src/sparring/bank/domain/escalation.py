from dataclasses import dataclass
from typing import NewType

EscalationId = NewType("EscalationId", str)


@dataclass(frozen=True)
class Escalation:
    """A conversation handed to a human support agent, with the reason the agent gave."""

    id: EscalationId
    reason: str
