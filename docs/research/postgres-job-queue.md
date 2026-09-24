# Postgres-backed job queue for Phase 4: hand-rolled `SKIP LOCKED` vs. procrastinate vs. pgqueuer

Question: given the Postgres-native worker decision (no Celery, no Redis) and the requirement of at-least-once delivery with idempotent handlers, should Phase 4 hand-roll a `FOR UPDATE SKIP LOCKED` queue table, or adopt a maintained library (procrastinate, pgqueuer, or another current one)?

Only two maintained Postgres-native Python job-queue libraries survived verification: **procrastinate** and **pgqueuer**. Other candidates found via search (`malthe/pq`, `DanielCollins/pgtq`, `vinissimus/jobs`) were checked against the GitHub API and are not actively maintained (last push 2024-08-03, 2019-04-28, and 2020-10-23 respectively), so they are excluded per the "do not pad the list" instruction and are not analyzed further.

## Comparison table

| Criterion | Hand-rolled `FOR UPDATE SKIP LOCKED` | procrastinate | pgqueuer |
|---|---|---|---|
| Delivery guarantee (as documented) | Not a library claim: Postgres gives row-skip locking only, no queue semantics; at-least-once is whatever you build | Not stated as a formal "at-least-once" claim in the docs pages read; behavior (stalled-job retry re-runs `doing` jobs) implies at-least-once, and the docs warn a too-short stall window "may lead to wrongly retrying jobs that are still running" | Not stated as a formal named guarantee either, but documented crash-recovery behavior is explicitly at-least-once: "A recovered job **will run again from the start**. Ensure your job functions are idempotent." |
| Visibility timeout / lease on worker death | None, you build it (e.g. `picked_at`/`locked_by` column plus a reaper query) | Heartbeat every 10s (default); a job is "stalled" once its worker's heartbeat is unseen for 30s (default); a periodic task `retry_stalled_jobs` (recommended every 10 min) finds `doing` jobs from stalled workers and calls `retry_job()` | `heartbeat_timeout` setting; heartbeats sent automatically at half that interval; PgQueuer itself "re-picks jobs whose heartbeat is older than `heartbeat_timeout`", no separate reaper task needed |
| Retries / backoff | None, you build counters and a backoff formula | `RetryStrategy(max_attempts, wait, linear_wait, exponential_wait, retry_exceptions)`; e.g. `exponential_wait=5` waits 5s, 25s, 125s, and so on; no max means retried indefinitely | `DatabaseRetryEntrypointExecutor` with `max_attempts` (default 5), `initial_delay` (default 1s), `max_delay` (default 5m), `backoff_multiplier` (default 2.0); doc example: 1s, 2s, 4s, 8s, 16s, 32s, then capped at 60s |
| Idempotency mechanism | None, you build a unique key / dedup table yourself | Documented "queueing lock": a job cannot be enqueued with `todo` status if another job shares the same queueing-lock value, raises `AlreadyEnqueued`; note it does not block a second `doing` job with the same lock, so it dedupes enqueue, not necessarily concurrent execution | Explicit `dedupe_key` parameter: "Pass a `dedupe_key` to prevent duplicate jobs from entering the queue... calling it twice with the same key and payload is safe." Constraint covers only `queued`/`picked` states; released once terminal. No built-in dead-letter queue, but poison jobs can be found by query |
| LISTEN/NOTIFY vs polling | You choose/build either; Postgres gives you the primitives only | Uses `LISTEN` ("PostgreSQL's LISTEN allows us to be notified whenever a task is available") with a `fetch_job_polling_interval` fallback poll and a separate `abort_job_polling_interval` for cancellation polling | Primary wakeup is `LISTEN/NOTIFY` triggered on insert ("wakes workers the moment a job lands"), with a polling fallback documented as a safety net |
| Postgres versions supported | Whatever your Postgres major version supports; `SKIP LOCKED` exists since Postgres 9.5, `LISTEN/NOTIFY` far longer, both covered by 16.15 and CI's `pgvector/pgvector:0.8.6-pg16` | "PostgreSQL 13+" (README, GitHub repo description), covers 16.15 and the pg16 CI image | "PostgreSQL 13+" ("PgQueuer targets Python 3.10+ and PostgreSQL 13+"), covers 16.15 and the pg16 CI image |
| Sync vs async, drivers | Your choice: psycopg2, psycopg3, or asyncpg, whichever the rest of the engine already uses | Both: `SyncPsycopgConnector` and `PsycopgConnector` (psycopg3-based) documented; the project statement is "used within both sync and async code" | Async-first ("built on asyncio"); drivers are asyncpg and psycopg, in both async and sync modes per the repo's driver docs |
| Latest release / date (read 2026-09-23) | n/a | 3.10.0, released 2026-09-23 (PyPI) | 1.4.0, released 2026-09-14 (PyPI) |
| Repo activity (read 2026-09-23) | n/a | `pushed_at`: 2026-09-23T21:58:35Z (GitHub API), not archived | `pushed_at`: 2026-09-17T13:02:00Z (GitHub API), not archived |
| License | n/a | MIT (GitHub API `license.spdx_id`) | MIT (GitHub API `license.spdx_id`) |

## Hand-rolled `FOR UPDATE SKIP LOCKED`

PostgreSQL's own documentation for `SELECT ... FOR UPDATE` describes the mechanism precisely: "With `SKIP LOCKED`, any selected rows that cannot be immediately locked are skipped," and clarifies that "`NOWAIT` and `SKIP LOCKED` apply only to the row-level lock(s), the required `ROW SHARE` table-level lock is still taken in the ordinary way." Postgres itself flags the intended use and the trade-off: "Skipping locked rows provides an inconsistent view of the data, so this is not suitable for general purpose work, but can be used to avoid lock contention with multiple consumers accessing a queue-like table." This is exactly the queue-table pattern; Postgres officially blesses it for that narrow purpose and no other.

`LISTEN`/`NOTIFY`, per Postgres's own docs, gives transactional, ordered, deduplicated delivery within a session lifetime: "if a `NOTIFY` is executed inside a transaction, the notify events are not delivered until and unless the transaction is committed"; ordering is guaranteed "except for dropping later instances of duplicate notifications"; and if the per-backend notification queue fills, "transactions calling `NOTIFY` will fail at commit" rather than silently dropping a payload. Payloads are capped: "In the default configuration it must be shorter than 8000 bytes." None of this constitutes a job-queue guarantee by itself, it's just a wakeup channel, and a listening worker that is down when `NOTIFY` fires simply never sees that event (Postgres does not persist or replay missed notifications to a disconnected listener).

Building on these two primitives, a hand-rolled queue gets nothing else for free. You would have to build, and separately test:

- **Lease / visibility timeout**: a `locked_by`/`locked_at` (or `status` plus `heartbeat_at`) column, a reaper query or periodic job to find rows whose lock is older than some threshold and reclaim them, and a decision about what "worker death" means (no heartbeat update vs. a hard TTL).
- **Retries and backoff**: an `attempts` column, a policy (fixed, linear, or exponential) implemented in application code, a cap, and a place to record failure reasons.
- **Idempotency key handling**: a unique/partial-unique index (e.g. on `(dedupe_key) WHERE status IN ('queued','picked')`) plus the enqueue-time conflict handling (`ON CONFLICT DO NOTHING`/`DO UPDATE`) and the state-transition logic to release the key on terminal states, essentially re-deriving what pgqueuer's `dedupe_key` already does.
- **Dead-letter handling**: a status or table for jobs that exhaust retries, plus operational tooling to inspect and requeue them.
- **Wakeup discipline**: since a disconnected `LISTEN`er misses events, you still need a polling fallback loop for correctness, with `LISTEN/NOTIFY` only as a latency optimization, the same design procrastinate and pgqueuer both already ship.

This is the same reasoning procrastinate's and pgqueuer's own architecture already encode; you would be reimplementing their heartbeat, dedupe, and backoff modules with less test coverage and no upstream maintenance.

## procrastinate

Procrastinate is an MIT-licensed, actively maintained (latest release 3.10.0, 2026-09-23, same day as this research) Python task-queue library targeting "PostgreSQL 13+" and usable "within both sync and async code" via `SyncPsycopgConnector`/`PsycopgConnector` (psycopg3-based).

Reliability model: workers send a heartbeat every 10 seconds by default; a job is considered stalled once its worker's heartbeat has not been seen for 30 seconds by default (both configurable). A periodic built-in task, `retry_stalled_jobs`, finds jobs in `doing` status belonging to stalled workers and calls `retry_job()` on them, this is opt-in machinery you must schedule yourself (e.g. via procrastinate's own cron feature), not automatic. The docs explicitly caution that too aggressive a stall window "may lead to wrongly retrying jobs that are still running," i.e. the retry behavior is at-least-once by construction and the library expects idempotent handlers, though the docs pages read did not contain the literal phrase "at-least-once."

Retries/backoff: `RetryStrategy` accepts `max_attempts`, `wait` (fixed), `linear_wait`, and `exponential_wait`, combinable, with `retry_exceptions` to scope which exceptions trigger a retry; unset `max_attempts` retries indefinitely.

Idempotency: procrastinate's primitive is the "queueing lock", a job cannot be enqueued in `todo` status while another job shares the same lock value (`AlreadyEnqueued` is raised on the second attempt). It targets duplicate enqueue, not necessarily duplicate concurrent execution (the docs say multiple jobs can simultaneously be `doing` under the same lock unless combined with the separate execution-lock feature), read this carefully against Phase 4's idempotency requirement.

Wakeup: `LISTEN`/`NOTIFY` is the documented mechanism ("PostgreSQL's LISTEN allows us to be notified whenever a task is available"), with `fetch_job_polling_interval` as the polling fallback and a distinct `abort_job_polling_interval` for cancellation checks.

## pgqueuer

PgQueuer is an MIT-licensed, actively maintained (latest release 1.4.0, 2026-09-14; repo pushed 2026-09-17) Python job queue "targets Python 3.10+ and PostgreSQL 13+," async-first ("built on asyncio") with both asyncpg and psycopg drivers documented in sync and async modes.

Concurrency safety is `FOR UPDATE SKIP LOCKED` directly ("workers claim jobs with `FOR UPDATE SKIP LOCKED` (never double-processed)"), the same Postgres primitive as the hand-rolled option, but packaged with the surrounding machinery.

Reliability model, from the repo's own `docs/guides/reliability.md` and `docs/guides/heartbeat.md`: "If a worker process crashes mid-job, the job remains in `picked` state with a stale heartbeat" and "Any worker can then claim and re-run the stalled job" once its heartbeat exceeds `heartbeat_timeout` (heartbeats are sent automatically at half that interval), PgQueuer re-picks such jobs itself, with no separate reaper task to schedule. The docs are explicit and unambiguous about the delivery consequence: "A recovered job **will run again from the start**. Ensure your job functions are idempotent, or checkpoint progress externally so a restart is safe." This is the clearest at-least-once statement found across either library.

Retries/backoff: `DatabaseRetryEntrypointExecutor` with `max_attempts` (default 5), `initial_delay` (default 1s), `max_delay` (default 5m), `backoff_multiplier` (default 2.0), documented example progression 1s, 2s, 4s, 8s, 16s, 32s, capped at 60s. `job.attempts` tracks the count (0-indexed).

Idempotency: an explicit `dedupe_key` parameter, "Pass a `dedupe_key` to prevent duplicate jobs from entering the queue... This turns enqueue into an idempotent operation: calling it twice with the same key and payload is safe." The uniqueness constraint applies only while the job is `queued`/`picked`; it releases on any terminal state (`successful`, `exception`, `canceled`, `deleted`, `failed`/held). Within a single batch enqueue call, duplicate `dedupe_key`s keep only the first occurrence by input order. No built-in dead-letter queue, but the docs note poison jobs (jobs that keep failing) can be found and handled via a direct query, and there's a documented `on_failure="hold"` mode to hold failed jobs for inspection rather than silently dropping them.

Wakeup: `LISTEN`/`NOTIFY` triggered by a database trigger on insert is the primary path ("PostgreSQL `LISTEN/NOTIFY` pushes jobs to workers instantly... A trigger fires notifications on queue inserts"), with polling as a documented fallback.

## Sources

All fetched/read 2026-09-23.

- PostgreSQL 16 documentation, "SELECT" (`FOR UPDATE`/`SKIP LOCKED` semantics): https://www.postgresql.org/docs/16/sql-select.html#SQL-FOR-UPDATE-SHARE, PostgreSQL 16 docs.
- PostgreSQL 16 documentation, "NOTIFY" (LISTEN/NOTIFY semantics, transactional delivery, ordering, dedup, queue-full failure, 8000-byte payload limit): https://www.postgresql.org/docs/16/sql-notify.html, PostgreSQL 16 docs.
- Procrastinate documentation, home/overview page: https://procrastinate.readthedocs.io/en/stable/, procrastinate stable docs (version 3.10.0 at read time).
- Procrastinate GitHub repository (README, description, PostgreSQL/Python support): https://github.com/procrastinate-org/procrastinate, read via raw README and repo page.
- Procrastinate GitHub Releases: https://github.com/procrastinate-org/procrastinate/releases, latest 3.10.0, 2026-09-23.
- Procrastinate on PyPI: https://pypi.org/project/procrastinate/, latest 3.10.0, released Sep 23, 2026.
- GitHub API, `repos/procrastinate-org/procrastinate` (pushed_at, license, archived), via `gh api`: `pushed_at 2026-09-23T21:58:35Z`, `license MIT`, not archived.
- Procrastinate docs, "Retry stalled jobs": https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html, heartbeat interval (10s default), stall threshold (30s default), `retry_stalled_jobs` behavior.
- Procrastinate docs, "Ensure tasks don't accumulate in the queue" (queueing locks): https://procrastinate.readthedocs.io/en/stable/howto/advanced/queueing_locks.html, `AlreadyEnqueued`, queueing-lock semantics.
- Procrastinate docs, "Define a retry strategy on a task": https://procrastinate.readthedocs.io/en/stable/howto/advanced/retry.html, `RetryStrategy` parameters (`max_attempts`, `wait`, `linear_wait`, `exponential_wait`, `retry_exceptions`).
- Procrastinate docs, how-to index: https://procrastinate.readthedocs.io/en/stable/howto_index.html, listing of production/advanced guide topics.
- PgQueuer GitHub repository (README/description): https://github.com/janbjorge/pgqueuer, and raw README: https://raw.githubusercontent.com/janbjorge/pgqueuer/main/README.md.
- PgQueuer GitHub Releases: https://github.com/janbjorge/pgqueuer/releases, latest v1.4.0 (Sep 14), with v1.3.x/v1.2.0 history.
- PgQueuer on PyPI: https://pypi.org/project/pgqueuer/, latest 1.4.0, released Sep 14, 2026.
- GitHub API, `repos/janbjorge/pgqueuer` (pushed_at, license, archived), via `gh api`: `pushed_at 2026-09-17T13:02:00Z`, `license MIT`, not archived.
- PgQueuer docs site, landing page: https://janbjorge.github.io/pgqueuer/, Python 3.10+/PostgreSQL 13+ support statement, `FOR UPDATE SKIP LOCKED` and LISTEN/NOTIFY-with-polling-fallback claims.
- PgQueuer repo docs, `docs/guides/reliability.md`: https://raw.githubusercontent.com/janbjorge/pgqueuer/main/docs/guides/reliability.md, crash-recovery/at-least-once statement, `dedupe_key` idempotency mechanism, no built-in dead-letter queue.
- PgQueuer repo docs, `docs/guides/heartbeat.md`: https://raw.githubusercontent.com/janbjorge/pgqueuer/main/docs/guides/heartbeat.md, heartbeat interval and stalled-job re-pick mechanism.
- PgQueuer repo docs, `docs/guides/retry.md`: https://raw.githubusercontent.com/janbjorge/pgqueuer/main/docs/guides/retry.md, `DatabaseRetryEntrypointExecutor` defaults and backoff formula.
- PgQueuer repo docs, `docs/guides/concurrency-control.md`: https://raw.githubusercontent.com/janbjorge/pgqueuer/main/docs/guides/concurrency-control.md, read, but contained no FOR UPDATE SKIP LOCKED / LISTEN-NOTIFY / dedup detail (noted as absent, not used as a positive source).
- GitHub API, `repos/malthe/pq`, `repos/DanielCollins/pgtq`, `repos/vinissimus/jobs` (pushed_at, archived), via `gh api`: used only to confirm these are not actively maintained (pushed 2024-08-03, 2019-04-28, 2020-10-23 respectively) and therefore excluded from the comparison.

Unverified / not found in primary sources read: a literal, formally labeled "at-least-once" (or "at-most-once"/"exactly-once") claim in either library's own docs. Both libraries' documented crash-recovery behavior implies at-least-once, but neither page fetched used that exact vocabulary. PgQueuer's stated PostgreSQL compatibility test matrix (beyond "13+") against the CI `pgvector/pgvector:0.8.6-pg16` image specifically is unverified, the "13+" claim covers 16.x by inclusion but no page read enumerated tested versions.

## Lean

The evidence supports not hand-rolling. Postgres's own docs are explicit that `SKIP LOCKED` provides only row-skip locking for a queue-like table and nothing about leases, retries, backoff, idempotency, or dead-lettering, all of that would have to be designed, built, and tested from scratch, largely reproducing what pgqueuer already ships (its `FOR UPDATE SKIP LOCKED` claim uses this exact primitive under the hood).

Between the two libraries, pgqueuer is the better fit specifically because of the idempotency requirement: it has a first-class, explicitly documented `dedupe_key` parameter described as making "enqueue... an idempotent operation," plus an unambiguous documented statement that a recovered job "will run again from the start" and handlers must be idempotent, this maps directly onto the stated requirement. Procrastinate's nearest equivalent, the queueing lock, is documented as deduplicating enqueue into `todo` state, not necessarily concurrent `doing` execution, which is a weaker and more indirect idempotency primitive for this use case. pgqueuer's async-first design with asyncpg/psycopg support also fits a Postgres-only stack cleanly, and both libraries state "PostgreSQL 13+," which covers the local 16.15 cluster and CI's pinned `pgvector/pgvector:0.8.6-pg16` image.

Caveat: pgqueuer's release cadence and community size (based on releases/commit history read) look smaller and newer than procrastinate's, and this research did not verify either library's compatibility against pgvector-specific extensions or against Postgres versions beyond the "13+" floor claim, worth a smoke test against the actual CI image before committing. If procrastinate's richer production tooling (cron, blueprints, middleware, more mature docs) matters more than the idempotency-key ergonomics, that would be a reasonable basis to pick it instead; the evidence does not make that choice a clear loss either way, but on the stated idempotent-handler requirement specifically, pgqueuer's documentation is the more directly on-point fit.
