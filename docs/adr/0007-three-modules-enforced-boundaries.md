# One package, three modules, boundaries enforced by import-linter

The code is a single Python package with three top-level modules: `bank` (domain and tool server), `agent` (the support agent) and `evaluation` (Scenarios, Simulated User, Oracle, Promotion, Regression Check). Dependency rules are checked in CI by `import-linter`: `bank` imports neither of the others; `agent` never imports `bank` and reaches it only through the tool protocol, which proves the agent works against the tool contract alone; `evaluation` may import `bank.domain` (the Oracle reuses Hard Rules and seeds initial state) and drives `agent` only through its public interface; nothing imports `evaluation`.

## Considered Options

- **uv workspace with three packages**: boundaries become declared dependencies, but three configs and cross-package versioning is ceremony a one-person project does not need.
