# LangGraph pause and resume on Postgres, facts only

Research for issue #149. Facts only, no recommendation: the decision is
tracked separately as issue #155.

All sources read 2026-09-23. Primary sources only: PyPI package pages, the
`langchain-ai/langgraph` GitHub repository (`main` branch), and the official
LangChain reference and docs sites (`docs.langchain.com`,
`reference.langchain.com`), which publish the current LangGraph documentation.

## Versions

- `langgraph`: **1.2.12**, released 2026-09-21, on PyPI
  (https://pypi.org/project/langgraph/, read 2026-09-23). Requires Python
  >=3.10; supports CPython and PyPy for 3.10 to 3.13.
- `langgraph-checkpoint-postgres`: **3.1.2**, released 2026-08-07, on PyPI
  (https://pypi.org/project/langgraph-checkpoint-postgres/, read 2026-09-23).
  Requires Python >=3.10, MIT license.
- Both packages' PyPI pages show a release history back to early 2024,
  including yanked releases (`langgraph` 1.2.3 for an "unintended merging
  strategy regression", `langgraph` 1.1.7 for "introduced bug with custom
  callback handlers").

## Dependency footprint

From `pyproject.toml` on the `main` branch of `langchain-ai/langgraph`
(read 2026-09-23):

- `langgraph` 1.2.12 runtime dependencies: `langchain-core>=1.4.7,<2`,
  `langgraph-checkpoint>=4.1.0,<5.0.0`, `langgraph-sdk>=0.4.2,<0.5.0`,
  `langgraph-prebuilt>=1.1.0,<1.2.0`, `xxhash>=3.5.0`, `pydantic>=2.7.4`.
- `langgraph-checkpoint-postgres` 3.1.2 runtime dependencies:
  `langgraph-checkpoint>=4.1.0,<5.0.0`, `orjson>=3.11.5`, `psycopg>=3.2.0`,
  `psycopg-pool>=3.2.0`. No optional extras defined.
- So a Postgres-backed LangGraph install pulls in `langchain-core`, the
  abstract `langgraph-checkpoint` package, `langgraph-sdk`,
  `langgraph-prebuilt`, `pydantic`, `xxhash`, `psycopg` (v3) and
  `psycopg-pool`, plus `orjson` for the Postgres checkpointer's
  serialization.

## Postgres checkpointer: package, schema, versions

Source: `libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py` and
`libs/checkpoint-postgres/README.md` on `main`
(https://github.com/langchain-ai/langgraph, read 2026-09-23).

- The package `langgraph-checkpoint-postgres` provides `PostgresSaver`
  (sync), `AsyncPostgresSaver` (async), and `ShallowPostgresSaver`
  (mentioned in the docs index; its details were not surfaced in the pages
  fetched, unverified beyond its name).
- The `MIGRATIONS` list in `base.py` creates four tables:
  - `checkpoint_migrations`: schema-version tracker, one integer primary
    key column.
  - `checkpoints`: `thread_id`, `checkpoint_ns`, `checkpoint_id`,
    `parent_checkpoint_id`, `type`, `checkpoint` (JSONB), `metadata`
    (JSONB); primary key `(thread_id, checkpoint_ns, checkpoint_id)`.
  - `checkpoint_blobs`: `thread_id`, `checkpoint_ns`, `channel`,
    `version`, `type`, `blob` (BYTEA); primary key
    `(thread_id, checkpoint_ns, channel, version)`.
  - `checkpoint_writes`: `thread_id`, `checkpoint_ns`, `checkpoint_id`,
    `task_id`, `idx`, `channel`, `type`, `blob` (BYTEA); primary key
    `(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)`.
  - A later migration adds a `task_path` column to `checkpoint_writes`
    with a default value, and later migrations use
    `CREATE INDEX CONCURRENTLY IF NOT EXISTS`.
- `.setup()` must be called once, before first use, to create these
  tables. The README warns that a manually created `psycopg` connection
  needs `autocommit=True` and `row_factory=dict_row`, or table creation
  and later reads can fail.
- Postgres version: no explicit minimum version is stated in the docs or
  README read. The package's own test matrix
  (`libs/checkpoint-postgres/Makefile` on `main`) runs CI against
  `POSTGRES_VERSIONS ?= 15 16`, with `POSTGRES_VERSION` defaulting to 16.
  `CREATE INDEX CONCURRENTLY IF NOT EXISTS`, used in a later migration,
  requires Postgres 9.2+, which is far below anything tested. Treat "15
  and 16 are what CI exercises" as the fact; "the minimum supported
  version" is unverified.

## How `interrupt()` and resume work

Sources: `docs.langchain.com/oss/python/langgraph/interrupts` and
`reference.langchain.com/python/langgraph/types/interrupt`, read
2026-09-23.

- `interrupt(value)` is called inside a node with a JSON-serializable
  value. It works by raising a special exception that the LangGraph
  runtime catches; the runtime then persists the graph state via the
  checkpointer and suspends execution.
- A checkpointer is required: without one there is no state to pause and
  resume, and the graph cannot suspend.
- The suspended run is identified by `thread_id`, passed as
  `{"configurable": {"thread_id": "..."}}` in the run config. Reusing the
  same `thread_id` resumes that checkpoint; a new one starts a fresh run.
- Resuming uses `Command(resume=<value>)`, invoked with the same
  `thread_id` config. The resume value becomes the return value of the
  original `interrupt()` call inside the node. For multiple simultaneous
  interrupts in one node, `Command(resume={interrupt_id: value, ...})`
  maps values to interrupt IDs.
- Replay semantics: on resume, **the entire node containing the
  `interrupt()` call reruns from the top**, not from the exact line where
  it paused. Any code in that node that ran before the `interrupt()` call
  runs again. The docs' explicit consequence: side effects before an
  `interrupt()` inside the same node must be idempotent, or moved after
  it.
- Documented caveats: do not wrap `interrupt()` in a bare `try/except`
  (it depends on the exception propagating); do not conditionally skip or
  reorder `interrupt()` calls within a node (resume matching is
  index-based); do not loop `interrupt()` inside non-deterministic
  control flow such as `while True`, use conditional edges instead.

## Durability modes

Source: `reference.langchain.com/python/langgraph/types/Durability`, read
2026-09-23. `Durability = Literal['sync', 'async', 'exit']`, described as
the "durability mode for the graph execution":

- `sync`: changes are persisted synchronously before the next step
  starts.
- `async`: changes are persisted asynchronously while the next step
  executes.
- `exit`: changes are persisted only when the graph exits.

No default value was stated on the page read. This parameter governs how
often intermediate state hits Postgres, independent of the
`interrupt`/`Command(resume=...)` mechanism above, which always requires a
checkpoint write to suspend.

## Task idempotency and resume (relevant to at-least-once delivery)

Source: `docs.langchain.com/oss/python/langgraph/functional-api`, read
2026-09-23 (this is the page that documents LangGraph's `@task` construct,
which wraps a discrete unit of work such as an API call):

- "A task represents a discrete unit of work, such as an API call or data
  processing step."
- "A task that started but did not finish may run again on that resume,
  so design side effects to be idempotent."
- The docs recommend idempotency keys, upserts, or verification of
  existing results as the mechanism to make a re-run of a task safe.

No official LangGraph doc page fetched (`docs.langchain.com/oss/python/
langgraph/fault-tolerance`, `.../durable-execution`, `.../persistence`)
states an explicit "at-least-once" or "exactly-once" execution guarantee
in those words for whole-graph or whole-node re-invocation from an
external caller. The concrete, sourced fact is the task-level statement
above: a task (and, by the interrupt docs above, the containing node) can
re-run on resume, and side effects must be made idempotent by the
application. Whether two external callers resuming the same `thread_id`
concurrently are prevented from both executing is **unverified against
primary sources**: no page fetched here documents built-in locking or
coordination across processes sharing one `thread_id`. (A secondary,
non-primary source, a vendor blog at diagrid.io, claims LangGraph has "no
built-in coordination to prevent both from executing" for concurrent
resumes of the same `thread_id`; this is not confirmed against an
official LangGraph source and is reported here only as unverified.)

## Fault tolerance: retries and node failure

Source: `docs.langchain.com/oss/python/langgraph/fault-tolerance` (via
search snippet and partial fetch, read 2026-09-23):

- LangGraph retries a node's failed attempt according to a retry policy
  (exception type, backoff settings); only after retries are exhausted
  does the graph's error handler run.
- On a `NodeTimeoutError`, LangGraph "clears any writes from the failed
  attempt, and lets the retry policy decide whether to retry."
- Failure context is checkpointed: "If the graph is interrupted or the
  process crashes after a node fails but before the handler completes,
  the handler sees the same `NodeError` context when the graph resumes
  from its checkpoint." (Quoted from the JavaScript docs mirror of the
  same fault-tolerance page,
  `docs.langchain.com/oss/javascript/langgraph/fault-tolerance`, read
  2026-09-23; the Python page's equivalent wording was not fully
  retrievable in this session, so cross-checked against the JS mirror
  which documents the same underlying behavior.)

## What the same pause looks like as a status column plus a worker

This section states facts about the plain-code alternative as read from
this repository's own existing pattern, not from a third-party source
(no primary source outside the repo applies to a codebase-internal
design):

- The repo's `docmatch` engine already uses a Postgres-backed worker
  model (per `CLAUDE.md`'s stack section: "a Postgres-backed worker").
  The pattern implied by the ticket is: a document row carries a status
  column (e.g. `needs_review`, `approved`), a human action flips the
  status via the review UI or API, and a worker process polls or is
  notified (e.g. `LISTEN`/`NOTIFY`, or a polling query with
  `SELECT ... FOR UPDATE SKIP LOCKED`) for rows in `approved` state to
  continue processing.
- This is a description of a generic pattern, not a documented LangGraph
  feature; it is included here as the point of comparison the ticket
  asks for, not as a sourced claim about LangGraph.

## Sources read (version and date)

- https://pypi.org/project/langgraph/, read 2026-09-23, version 1.2.12
  (released 2026-09-21).
- https://pypi.org/project/langgraph-checkpoint-postgres/, read
  2026-09-23, version 3.1.2 (released 2026-08-07).
- https://github.com/langchain-ai/langgraph (branch `main`), files:
  `libs/checkpoint-postgres/pyproject.toml`,
  `libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py`,
  `libs/checkpoint-postgres/README.md`,
  `libs/checkpoint-postgres/Makefile`,
  `libs/langgraph/pyproject.toml`; read 2026-09-23.
- https://docs.langchain.com/oss/python/langgraph/interrupts, read
  2026-09-23.
- https://docs.langchain.com/oss/python/langgraph/functional-api, read
  2026-09-23.
- https://docs.langchain.com/oss/python/langgraph/persistence, read
  2026-09-23 (partial; the fetch tool returned limited content for this
  page).
- https://docs.langchain.com/oss/python/langgraph/durable-execution, read
  2026-09-23 (partial; durability modes not found on this specific page,
  found instead on the reference page below).
- https://docs.langchain.com/oss/python/langgraph/fault-tolerance and
  https://docs.langchain.com/oss/javascript/langgraph/fault-tolerance,
  read 2026-09-23 (partial on the Python page; JS mirror used for the
  quoted crash-resume sentence, documenting the same underlying
  behavior).
- https://reference.langchain.com/python/langgraph/types/Durability, read
  2026-09-23.
- https://reference.langchain.com/python/langgraph/types/interrupt, read
  2026-09-23.

## Gaps and unverified points

- No explicit minimum Postgres version was found in the docs or README;
  only the CI test matrix (15 and 16) is confirmed.
- No official LangGraph source fetched states an explicit at-least-once
  or exactly-once guarantee for concurrent resume of the same
  `thread_id` from an external queue; only the task-level "may run again
  on resume, design for idempotency" guidance is confirmed from a
  primary source.
- `ShallowPostgresSaver`'s behavior was not directly verified beyond its
  name appearing in the docs index.
- The default `Durability` value was not stated on the page read.
- The Python-specific wording of the fault-tolerance crash/resume
  behavior was not fully retrievable in this session; the JS docs mirror
  was used instead, on the basis that both language SDKs document the
  same runtime behavior, but this substitution itself is unverified.
