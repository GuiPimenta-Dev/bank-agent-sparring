# Bank repositories have an in-memory and a Postgres adapter, held equal by contract tests

Exploration runs hundreds of conversations in parallel, each needing its own bank seeded from a Bank Snapshot and read back for the Oracle, while the deployed service needs durable storage. The bank therefore has a repository port with two real adapters: in-memory (one fresh bank per conversation, also used by unit tests) and Postgres (deployed service), with one Unit of Work per tool call. A single contract test suite, parametrized over both adapters, keeps them behaviourally identical, and Hard Rules always live in the domain — database constraints are only defense in depth — so the in-memory adapter can never silently skip a rule.

## Considered Options

- **Postgres everywhere** (schema or transaction per conversation): realistic, but slow and awkward under heavy parallelism.
- **SQLite everywhere**: one adapter, but undersells the production story.
