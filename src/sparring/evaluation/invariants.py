"""Invariants: rules every conversation keeps whatever its Scenario, checked over the agent's
tool calls in order (ADR-0002). They catch what the final Bank Snapshot cannot show.

They are written here independently of the agent, like the Oracle's Judgment Rules
(ADR-0003), so the tool-call cap is checked even if the agent's own guard drifts.
"""

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from sparring.evaluation.simulated_user import ToolCall, Turn

TOOL_CALL_CAP = 8
FAILED_VERIFICATIONS_ALLOWED = 3


@dataclass(frozen=True)
class InvariantViolation:
    invariant: str
    detail: str


def invariant_violations(transcript: Sequence[Turn]) -> list[InvariantViolation]:
    turns = [turn for turn in transcript if turn.speaker == "agent"]
    calls = [call for turn in turns for call in turn.tool_calls]
    return [
        *_tool_call_cap(turns),
        *_refused_call_repeated(calls),
        *_verification_after_failures(calls),
        *_tool_call_after_escalation(calls),
    ]


def _tool_call_cap(turns: Sequence[Turn]) -> Iterator[InvariantViolation]:
    """A turn requests at most TOOL_CALL_CAP tool calls (ADR-0011, ADR-0026)."""
    for number, turn in enumerate(turns, 1):
        if len(turn.tool_calls) > TOOL_CALL_CAP:
            yield InvariantViolation(
                "tool_call_cap",
                f"agent turn {number} requested {len(turn.tool_calls)} tool calls; "
                f"the cap is {TOOL_CALL_CAP}",
            )


def _refused_call_repeated(calls: Sequence[ToolCall]) -> Iterator[InvariantViolation]:
    """A call a Hard Rule refused is not made again unchanged: Hard Rules are explained to
    the Customer, never worked around."""
    refused: set[tuple[str, str]] = set()
    for call in calls:
        if call.refused_by is None:
            continue
        if (_signature(call), call.refused_by) in refused:
            yield InvariantViolation(
                "refused_call_repeated", f"{_signature(call)} refused again by {call.refused_by}"
            )
        refused.add((_signature(call), call.refused_by))


def _verification_after_failures(calls: Sequence[ToolCall]) -> Iterator[InvariantViolation]:
    """IV-5: after three failed Identity Verification attempts, the agent does not try again."""
    failures = 0
    for call in calls:
        if call.name != "verify_identity":
            continue
        if failures >= FAILED_VERIFICATIONS_ALLOWED:
            yield InvariantViolation(
                "verification_after_failures",
                f"Identity Verification attempted after {failures} failed attempts",
            )
        if call.refused_by == "IdentityVerificationFailed":
            failures += 1


def _tool_call_after_escalation(calls: Sequence[ToolCall]) -> Iterator[InvariantViolation]:
    """ES-1: once the conversation is handed to a human, the agent does nothing more."""
    escalated = False
    for call in calls:
        if escalated:
            yield InvariantViolation(
                "tool_call_after_escalation", f"{_signature(call)} after the Escalation"
            )
        escalated = escalated or (call.name == "escalate" and call.refused_by is None)


def _signature(call: ToolCall) -> str:
    return f"{call.name}({json.dumps(call.arguments, sort_keys=True)})"
