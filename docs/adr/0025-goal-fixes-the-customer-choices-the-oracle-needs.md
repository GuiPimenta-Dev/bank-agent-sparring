# The Goal fixes every Customer choice the Oracle depends on

Some Judgment Rules only tell the agent to offer something or to honour a request: offer a Freeze for a transaction the Customer does not recognise (DS-7), honour an explicit request for a human (ES-2). The Bank Snapshot shows what the Customer accepted, not what the agent offered, so the Oracle can judge these rules only if it knows the Customer's answer in advance. The Goal therefore fixes it. A Customer who disputes transactions they do not recognise accepts the Freeze the agent must offer. A Customer asks for a human only when the Goal is to Escalate, and the Simulated User is told so. A missing Freeze or an unasked-for Escalation then shows up as a difference in the Bank Snapshot, so it remains a deterministic Failure (ADR-0002).

Escalation ends the conversation: once the bank records it, the Simulated User says nothing more. This makes "before any Escalation" observable, since a Freeze the agent did not get to first never happens.

## Considered Options

- **An LLM judge over the transcript for offers**: catches an offer the Customer declined, but its verdict could only flag and never promote (ADR-0002), so these rules would never grow the Golden Set.
- **An explicit `accepts_freeze` field on the Goal**: more general, but it doubles the Goal's shape for a choice no Customer in a fraud scenario would make differently.

## Consequences

Scenario narratives, hand-written or generated, must state these answers, and Exploration's narrative prompt must follow the same convention. A Persona who gets frustrated leaves the conversation instead of asking for a human, which is less realistic but keeps ES-2 predictable.
