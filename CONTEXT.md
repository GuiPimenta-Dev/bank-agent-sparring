# Sparring

A customer-support agent for Kestrel Bank, a fictional digital bank, limited to card operations. It acts on the bank's backend under written policies and is continuously stress-tested by simulated users whose failures feed a regression suite.

## Language

### Banking

**Customer**:
The holder of a bank account and its cards. A Simulated User may play a Customer or someone pretending to be one.
_Avoid_: user, client, cardholder

**Tier**:
The Customer's plan, Standard or Premium, which changes fees and thresholds the Policy applies.
_Avoid_: plan, level, segment

**Identity Verification**:
Proof that the person in the conversation is the Customer, required before any account data is revealed or any operation is performed.
_Avoid_: authentication, login, KYC

**Freeze**:
A temporary, reversible lock on a card, placed either by the Customer or by the bank's fraud team.
_Avoid_: block, lock, suspension

**Lost or Stolen Report**:
The permanent cancellation of a card, followed by the issuing of a Replacement Card.
_Avoid_: block, cancellation, card blocking

**Replacement Card**:
A new card with a new number, issued after a Lost or Stolen Report.
_Avoid_: reissue, new card

**Dispute**:
A Customer's claim that a settled transaction is wrong or was not authorized by them.
_Avoid_: chargeback, contestation, claim

**Provisional Credit**:
Money temporarily credited to the Customer while a Dispute is investigated.
_Avoid_: refund, reversal

**Escalation**:
Handing the conversation over to a human support agent.
_Avoid_: transfer, handoff

**Verified Session**:
The scope, opened by a successful Identity Verification, within which the agent may read and act on one Customer's data.
_Avoid_: login session, auth context

**Pending Action**:
An irreversible operation requested by the agent that the backend holds until the Customer gives a Confirmation.
_Avoid_: proposal, draft, pending operation

**Confirmation**:
The Customer's acceptance or rejection of a Pending Action, given through the Customer Channel and never through the agent.
_Avoid_: approval, consent

**Customer Channel**:
The bank's direct line to the Customer outside the conversation, such as the bank's app, where Pending Actions are shown and Confirmations given.
_Avoid_: app, push, out-of-band channel

**Policy**:
The written rules that govern what the agent may do, when, and for whom. Every rule is either a Hard Rule or a Judgment Rule.
_Avoid_: guidelines, rules, SOP

**Hard Rule**:
A Policy rule the bank's backend enforces by refusing the operation; the agent cannot break it, only handle the refusal.
_Avoid_: constraint, validation, guardrail

**Judgment Rule**:
A Policy rule that exists only in the Policy text and that the agent must choose to follow.
_Avoid_: soft rule, guideline, best practice

### Evaluation

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
