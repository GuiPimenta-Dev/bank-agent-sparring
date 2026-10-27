# Cost is a first-class constraint: hard caps, early stopping, cost per resolved conversation

This is a hobby project and must stay cheap. Every run (Exploration, Regression Check, model sweep) has a hard dollar cap computed in code from API-reported usage and a price table versioned in the repo; the CLI shows an estimate before starting, stops launching conversations at the cap, and lets in-flight ones finish. A Regression Check that exhausts its budget is invalid, never passing. The Regression Check stops early: each Scenario runs once and only failures run again, because a single pass already proves a Scenario is not Failing, which is the only blocking condition — cutting cost by roughly 60% without changing the gate (Flaky warnings become less precise). Every report includes cost per resolved conversation, the metric that chooses the agent's production model.

## Consequences

Scenario Status for a Scenario that passed on its first run is only known to be "not Failing"; full Passing/Flaky classification is computed where it matters, for Scenarios that were not Passing on the current version.
