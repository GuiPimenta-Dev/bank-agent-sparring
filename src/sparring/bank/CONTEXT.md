# Bank

Kestrel Bank, a fictional digital bank, limited to card operations: its domain, the Policy that governs the support agent, and the backend that enforces the Policy's Hard Rules.

## Language

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

**Fee**:
A charge the bank applies to a Customer, such as for a Replacement Card.
_Avoid_: charge, cost, price

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
