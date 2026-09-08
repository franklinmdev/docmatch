# docmatch, project instructions

Read `README.md` first. `docs/alternatives.md` records why this problem and what would justify a pivot.

## Mission

A document reconciliation engine: extract invoices with vision language models, validate with deterministic gates, match against purchase orders and receiving records, route exceptions to human review, and measure every change against a labeled benchmark in CI.

## Rules

1. **No product surface.** No auth, billing, tenancy, or marketing site. One CLI, one API, one review page.
2. **A phase is done only when its number is in the README** with the commit that produced it. The next phase does not start before that.
3. **Evals before features.** Every change to a model, prompt, threshold, or rule runs the eval on the fixed subset. A regression blocks merge.
4. **Models structure text; code decides.** Arithmetic, tolerances, matching, and approval are deterministic. Confidence never gates acceptance on its own.
5. **No time estimates in any document.** Phases and exit criteria, never dates, weeks, or hours.
6. **Never commit datasets, document contents, or personal data.** `data/` is ignored. Only loaders, generators, and aggregate numbers are committed.
7. **Smallest change that moves a number.** Prefer deleting a component over adding one.
8. **Docs before code.** Before writing code against a library, API, or service, read its current documentation and record the version you read. Nothing from memory.

## Stack

- Engine: Python 3.12, `uv`, Pydantic, provider SDKs with native structured output, Postgres with `pgvector` and `pg_trgm`, a Postgres-backed worker, FastAPI, Langfuse, pytest.
- Review UI: Next.js with TypeScript under `apps/review`, one page, timeboxed.
- Cut on purpose: schema DSLs, document-parsing SaaS as a foundation, Celery and Redis, judge-model eval frameworks. Graph orchestration is a phase 4 decision, not a default.

## Conventions

- Code, identifiers, commits, and docs in English.
- Conventional commits, small and frequent. The history is part of the deliverable.
- Tests live next to the code they test. The eval suite lives under `engine/tests/evals`.
- Every benchmark number in the README links to the commit and the command that produced it.

## Local files

`notes/` and `CLAUDE.local.md` are untracked and personal. Do not reference their contents in committed files.
