# Code Style

How code in `sparring` is written. Each rule points to the ADR that explains why; this file only says what.

## Tooling

- Python 3.13, managed with `uv`.
- `ruff` for linting and formatting; `pyright` in strict mode; `pytest`; `import-linter` for module boundaries.
- CI fails on any of them.

## Vocabulary

- Names of modules, classes, functions and variables use the terms in the context glossaries listed in `CONTEXT-MAP.md`. A word listed under _Avoid_ there is avoided in code too: `Scenario`, never `TestCase`; `promotion.py`, never `golden_ingest.py`.
- A concept that needs a name and has none in its context's `CONTEXT.md` gets one there first.

## Module boundaries

Enforced by `import-linter`, except the standard-library-only rule for `bank.domain`, which import-linter cannot express and a test checks instead ([ADR-0007](adr/0007-three-modules-enforced-boundaries.md), [ADR-0008](adr/0008-langgraph-contained-in-agent.md), [ADR-0014](adr/0014-stdlib-domain-pydantic-at-edges.md)):

| Module | May import | Must never import |
|---|---|---|
| `bank.domain` | the standard library | anything else |
| `bank` | its own layers, third-party libraries | `agent`, `evaluation` |
| `agent` | `langgraph`, `langchain_*`, third-party libraries | `bank`, `evaluation` |
| `evaluation` | `bank.domain`, the public interface of `agent` | `langgraph`, `langchain_*`, other parts of `bank` |

`agent` reaches the bank only through MCP. Nothing imports `evaluation`. Composition roots such as `sparring/chat.py` wire modules together and never import LangGraph or LangChain directly.

## Layout

```
src/sparring/
  bank/
    domain/        aggregates, value objects, domain services, HardRuleRefusal
    application/   async use cases and ports (Protocols)
    adapters/      in_memory.py, clock.py, mcp_server.py (postgres/ and the HTTP Customer Channel come later)
  agent/
    policy.md      the Policy text, used as the system prompt
    support_agent.py
    bank_tools.py  the bank's MCP tools as LangChain tools
  evaluation/
    one flat file per glossary concept: scenario.py, oracle.py, simulated_user.py, conversation.py, ...
  chat.py          composition root of the terminal chat
  run_scenario.py  composition root that runs one Scenario
  explore.py       composition root of Exploration
  failures.py      composition root of the Failure Signature report
  system_under_test.py  a fresh bank and agent per Scenario, shared by composition roots
  tracing.py       Langfuse set-up shared by composition roots
```

- Layers exist only in `bank`, because two entrypoints (MCP and the Customer Channel) share its use cases.
- `evaluation` stays flat until a file passes roughly 400 lines.

## Objects and composition

From [ADR-0016](adr/0016-protocol-ports-no-inheritance.md):

- Ports are `typing.Protocol`s. Adapters satisfy them structurally and never inherit from them.
- No class inheritance, except to define a `Protocol`, subclass `Exception`, or subclass Pydantic's `BaseModel`. To reuse behaviour, receive a collaborator through the constructor.
- Dependencies arrive through the constructor. There is no DI framework, no service locator, and no module-level singletons.
- Each entrypoint has one composition root that wires the object graph explicitly.
- A port exists only at a real boundary: repositories, `Clock`, the Simulated User's LLM client, Langfuse. A class with one implementation forever, such as a domain service, gets no Protocol.

## Domain modelling

- `bank.domain` uses only the standard library ([ADR-0014](adr/0014-stdlib-domain-pydantic-at-edges.md)).
- Ids are `NewType`s over `str`. Value objects are `@dataclass(frozen=True)`. Aggregates are plain classes whose methods protect their own Hard Rules.
- Aggregates are small and reference each other by id. A rule spanning several aggregates lives in a domain service, such as `DisputeEligibility`.
- Money is `Money` (a `Decimal` plus a currency), never `float`.
- Never call `datetime.now()` in `domain` or `application`. Time comes from the injected `Clock`.

## Boundaries and validation

- Pydantic is used only where untrusted data enters or a boundary is crossed: MCP tool arguments, Scenario and Bank Snapshot JSON, settings, and LLM structured outputs.
- Mapping between Pydantic models and domain objects lives in the adapter that owns the boundary.

## Async

- Async is a property of I/O, never of logic. The domain is synchronous.
- Use cases, repositories, MCP, LangGraph and LLM clients are async.
- Concurrency uses `asyncio.TaskGroup` with a semaphore that bounds parallelism.

## Errors

From [ADR-0017](adr/0017-error-handling-and-inconclusive.md):

- Hard Rule refusals raise a subclass of `HardRuleRefusal`. The hierarchy is at most two levels deep: the base and its leaves, with no intermediate classes.
- The MCP adapter catches `HardRuleRefusal` in exactly one place and returns a structured refusal.
- Invalid tool arguments return a structured error that tells the model what to fix.
- Unexpected exceptions reach the model as a generic error with no stack trace or internals. The details go to the Langfuse trace.
- A conversation interrupted by infrastructure after retries is Inconclusive, never a Failure.
- Never swallow an exception silently.

## Persistence

From [ADR-0009](adr/0009-in-memory-and-postgres-repositories.md) and [ADR-0015](adr/0015-sqlalchemy-core-not-orm.md):

- The Postgres adapter uses SQLAlchemy Core and Alembic. No ORM.
- Rows map to aggregates explicitly inside the adapter.
- There is one Unit of Work per tool call.
- Hard Rules live in the domain. Database constraints are only defense in depth.

## LLM code

- Prompts and the Policy text live in git, never in Langfuse Prompt Management ([ADR-0012](adr/0012-langfuse-system-of-record-prompts-in-git.md)).
- Model ids, effort levels and prices come from configuration and a versioned price table, never from literals scattered in code.
- Every LLM call records its usage so the run's cost cap can be enforced ([ADR-0020](adr/0020-cost-is-a-first-class-constraint.md)).

## Tests

- Tests use real test adapters (in-memory repositories, `FixedClock`, an in-memory Langfuse adapter), not mocks.
- The repository contract suite runs against every repository adapter.
- MCP tools are tested through an in-memory MCP client.
- No test calls a real LLM. Agent wiring is tested with a scripted fake model; agent behaviour is tested by the Golden Set.
- A change to `agent/policy.md` adds or changes Canonical Cases in the same PR ([ADR-0018](adr/0018-canonical-cases-guard-the-oracle.md)).

## Comments

- Comments explain why, not what. Code that needs a "what" comment needs better names.
- Docstrings go on ports, use cases and the public interface of `agent`, and nowhere by default.
- A deliberate simplification's known limit and upgrade path go in the PR's delivery report, not in a code comment.
- No `ponytail` comments, ever (checked in CI).
