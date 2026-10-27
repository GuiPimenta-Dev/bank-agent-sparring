# Evaluation

How the support agent is stress-tested: Simulated Users play Scenarios against it, the Oracle judges the outcome, and Failures grow a Golden Set that guards every change.

## Language

**Scenario**:
A test case for the agent: an initial backend state, a Persona, a Goal, and an Expected Outcome, all fixed before any conversation happens.
_Avoid_: test case, task, episode

**Persona**:
The character a Simulated User plays in a Scenario: archetype, temperament, and Patience.
_Avoid_: profile, character, user type

**Patience**:
How many turns a Persona tolerates without progress before giving up and ending the conversation.
_Avoid_: timeout, max turns

**Goal**:
What the Customer wants in a Scenario, held in two forms: a structured intent the Oracle reads and a narrative the Simulated User plays. Hidden from the agent.
_Avoid_: objective, task, intent

**Dimension**:
An explicit axis along which Scenarios vary, such as operation, customer tier, transaction state, amount band, transaction age, persona archetype or Tactic.
_Avoid_: feature, parameter, attribute

**Tactic**:
The adversarial technique a Scenario applies against the agent (impersonation, third party, prompt injection planted in backend data, pressure to bend a Judgment Rule), or none.
_Avoid_: attack, exploit, jailbreak

**Adaptive Sampling**:
Choosing Dimension values for new Scenarios in proportion to their observed Failure rate, with a floor so no value is starved.
_Avoid_: targeting, active learning, fuzzing

**Simulated User**:
An LLM that plays a Scenario's Persona and pursues its Goal in conversation with the agent.
_Avoid_: test agent, tester subagent, user bot

**Oracle**:
The deterministic implementation of the Policy that computes each Scenario's Expected Outcome. The only authority on what correct means.
_Avoid_: judge, grader, reference solution

**Bank Snapshot**:
The complete, serializable state of the bank at one moment; a Scenario's initial state and its Expected Outcome are both Bank Snapshots.
_Avoid_: dump, fixture, world state

**Canonical Case**:
A hand-written Scenario whose Expected Outcome was worked out by a human, used to verify the Oracle; the only human-labelled data in the system.
_Avoid_: fixture, gold label, unit test

**Expected Outcome**:
The Bank Snapshot that a correct handling of a Scenario must produce, as computed by the Oracle.
_Avoid_: ground truth, label, answer

**Invariant**:
A rule that must hold in every conversation regardless of Scenario, checked deterministically over the agent's sequence of tool calls.
_Avoid_: guardrail, constraint, check

**Failure**:
A conversation whose final backend state does not match its Scenario's Expected Outcome, or that violates an Invariant.
_Avoid_: error, bug, miss

**Inconclusive**:
A conversation cut short by an infrastructure failure after retries; it is neither a pass nor a Failure, is never promoted, and does not count toward Scenario Status.
_Avoid_: error, crash, timeout, flaky

**Golden Set**:
The regression suite of Scenarios with verified Expected Outcomes; it grows automatically from Failures and is append-only — Scenarios are archived, never edited or deleted.
_Avoid_: golden dataset, eval set, test suite

**Reproduction**:
Re-running a failed Scenario with fresh Simulated User samples to tell a real agent Failure from simulator noise.
_Avoid_: retry, rerun, replay

**Policy Drift**:
A disagreement between the Policy text and the Oracle, where the agent followed the text and the Oracle judged it wrong.
_Avoid_: oracle bug, spec mismatch

**Quarantine**:
The holding state of a Failure suspected of Policy Drift, awaiting a human decision instead of Promotion.
_Avoid_: review queue, pending, on hold

**Exploration**:
The scheduled search for new Failures by running freshly generated Scenarios against the current agent.
_Avoid_: simulation run, fuzzing, nightly

**Regression Check**:
Running the Golden Set against a proposed change to the agent and comparing each Scenario's Status with the current version's.
_Avoid_: CI eval, golden run, test run

**Scenario Status**:
How reliably one agent version handles one Golden Set Scenario across repeated runs: Passing (every run), Flaky (some runs) or Failing (no run).
_Avoid_: score, result, pass rate

**Regression**:
A Scenario that is Passing for the current agent version and Failing for a proposed change.
_Avoid_: break, degradation

**Failure Signature**:
A deterministic label for what went wrong in a Failure, derived from the Oracle's diff or the violated Invariant; Failures sharing a Signature are the same problem.
_Avoid_: cluster, root cause, error type

**Promotion**:
Adding a failed Scenario to the Golden Set once its Failure is deterministic, reproduced, and not quarantined.
_Avoid_: ingestion, upsert, approval
