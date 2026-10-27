# Policy is split into backend-enforced Hard Rules and agent-followed Judgment Rules

Every Policy rule is classified as either a Hard Rule, enforced by the bank's backend domain model (the tool refuses), or a Judgment Rule, which exists only in the Policy text the agent reads. Security-critical rules (Identity Verification via a Verified Session, transaction states, time windows, fraud-team Freezes) are Hard Rules, because a production bank would never leave them to an LLM. Judgment Rules (when to Escalate, disambiguating Freeze vs Lost or Stolen Report, refusing third parties) are where the agent can genuinely fail, so they are what the Oracle implements independently; Hard Rules the Oracle may reuse from the backend domain.

## Consequences

Data leakage before Identity Verification is impossible by construction, so the adversarial Scenarios shift to prompt injection through backend data, third parties after a legitimate verification, and pressure to bend Judgment Rules.
