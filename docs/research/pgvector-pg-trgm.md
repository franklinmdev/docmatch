# What pgvector and pg_trgm offer a four-arm retrieval benchmark

Answers issue #113, part of the Phase 3 map #111. The question: what pgvector
and pg_trgm actually offer for a four-arm retrieval benchmark (trigram only,
vector only, hybrid by reciprocal rank fusion, hybrid plus rerank) over a
catalog of short product descriptions in Postgres, at the scale that catalog
will actually be: a few thousand rows, not millions. The local cluster is
Postgres 16.15 on Ubuntu 24.04 in WSL2. CI will run a pinned pgvector
container against a small committed synthetic fixture.

All sources were read on 2026-09-20 unless a source states its own date, in
which case that date is given too. "Unverified" marks a claim no primary
source in this pass confirmed. Nothing under `data/` or from DocILE was used;
this file cites only vendor and project documentation, package archives, and
one academic paper. No installation was performed on the local cluster, per
ticket #115.

## 1. pgvector: types, indexes, and operators

### Vector types

Per the pgvector README (raw `master` branch, read 2026-09-20, current
release 0.8.6 as established in section 4 below):

- `vector`: single precision, up to 16,000 dimensions.
- `halfvec`: half precision (2 bytes per element), up to 16,000 dimensions,
  added in 0.7.0.
- `sparsevec`: sparse vectors, up to 16,000 non-zero elements, added in
  0.7.0.
- `bit`: Postgres's own bit type, usable with pgvector's binary distance
  operators (Hamming, Jaccard) once indexed with the `bit_*_ops` operator
  classes, added in 0.7.0.
- The README also notes `double precision[]` and `numeric[]` can hold vector
  data at full precision outside the `vector` type, for cases where 4-byte
  floats lose precision.

A catalog of a few thousand short product descriptions needs none of
`halfvec` or `sparsevec`: those exist for memory savings at large scale
(half precision) or for sparse embedding models. The dense `vector` type is
the correct fit here.

### Index types: HNSW versus IVFFlat

Both are approximate nearest neighbor indexes; pgvector also supports exact
search with no index at all (a sequential scan, sped up by increasing
`max_parallel_workers_per_gather` per the README).

**HNSW** (hierarchical navigable small world graph):

- Build parameters: `m`, "the max number of connections per layer" (default
  16), and `ef_construction`, "the size of the dynamic candidate list for
  constructing the graph" (default 64). The README states "a higher value of
  `ef_construction` provides better recall at the cost of index build time /
  insert speed."
- Query parameter: `hnsw.ef_search` (default 40). "A higher value provides
  better recall at the cost of speed."
- Build cost: no training step, so "an index can be created without any data
  in the table," unlike IVFFlat. Build is graph construction, which the
  README says "build[s] significantly faster when the graph fits into
  `maintenance_work_mem`." Parallel HNSW builds were added in 0.6.0
  (CHANGELOG, "Added support for parallel index builds for HNSW").
- Query cost: the README states HNSW has "better query performance than
  IVFFlat (in terms of speed-recall tradeoff)," i.e. it is the slower index
  to build and the faster one to query.

**IVFFlat** (inverted file with flat quantization):

- Build parameter: `lists`. The README's own rule of thumb is "rows / 1000
  for up to 1M rows and sqrt(rows) for over 1M rows."
- Query parameter: `probes` (default 1). "A higher value provides better
  recall at the cost of speed."
- Build cost: the README states IVFFlat has "faster build times and uses
  less memory than HNSW, but has lower query performance." It requires
  "some data" in the table before the index is built, because list centroids
  are trained from a sample of existing rows; building it against an empty
  or near-empty table gives poor clustering.
- Query cost: the README's own tradeoff, lower query performance than HNSW
  for the same recall target.

### At this catalog's scale (a few thousand rows, not millions)

The README's guidance is written with large catalogs in mind (its own
`lists` formula branches at 1 million rows). Applying it explicitly at a few
thousand rows: IVFFlat's `lists` rule of thumb (`rows / 1000`) gives a
single-digit number of lists for a catalog under 10,000 rows, which is too
coarse to cluster meaningfully. IVFFlat's chief documented advantage, faster
build and lower memory than HNSW, is not worth much at this size since both
indexes build in a small fraction of a second on a few thousand rows of
short text embeddings; the README gives no benchmark numbers at this scale,
so this is a judgment applied to the documented cost model, not a claim the
docs make directly. HNSW's documented downside, slower build, is negligible
at this row count, and its documented upside, better query recall per unit
of query latency, is the property this benchmark's latency-per-query number
actually measures. HNSW is the index worth using for the vector arm here,
and it has the added convenience of not needing pre-existing data to be
created (the README's own point), which matters for the CI fixture path
where the index and the six-document fixture are set up together.

### Distance operators

From the README (read 2026-09-20):

| Operator | Distance | Operator class family |
|---|---|---|
| `<->` | Euclidean (L2) | `vector_l2_ops` |
| `<#>` | negative inner product | `vector_ip_ops` |
| `<=>` | cosine distance | `vector_cosine_ops` |
| `<+>` | taxicab (L1), added 0.7.0 | `vector_l1_ops` |
| `<~>` | Hamming distance (bit vectors) | `bit_hamming_ops` |
| `<%>` | Jaccard distance (bit vectors) | `bit_jaccard_ops` |

Each index type (HNSW and IVFFlat) exposes matching operator classes, e.g.
`CREATE INDEX ON items USING hnsw (embedding vector_cosine_ops)` versus
`vector_l2_ops` versus `vector_ip_ops`, and the same names apply to IVFFlat
indexes. For `halfvec` and `sparsevec` the README says to use the
`halfvec_*_ops` and `sparsevec_*_ops` variants.

### Which operator suits a normalized embedding

The README states directly: "If vectors are normalized to length 1 (like
OpenAI embeddings), use inner product for best performance," using `<#>`
with `vector_ip_ops`. The reasoning behind why this is correct, not just
documented, is a property of the math that the README's example
demonstrates but does not spell out: for unit-length vectors, cosine
distance, squared Euclidean distance, and negative inner product all
produce the same ranking order, because cosine distance equals `1 -
dot_product` and squared L2 distance equals `2 - 2 * dot_product` when both
vectors have length 1. Inner product is cheapest to compute per comparison
because it skips normalization, which is why the README calls it fastest
"for best performance," not because it ranks differently.

This benchmark has not yet chosen its local embedding model (that is a
Phase 3 decision still open per map #111), so whether its output is
guaranteed unit-normalized is unverified here. The operationally safe
default is `<=>` with `vector_cosine_ops`, which gives the documented
correct ranking whether or not the model's output happens to be
pre-normalized, since it normalizes as part of the distance computation.
Once the chosen model's output is confirmed unit-normalized (checked
against that model's own documentation, not assumed), switching to `<#>`
with `vector_ip_ops` gives the same ranking at a lower per-comparison cost,
per the README's own guidance.

### Correct index and operator pairing

`CREATE INDEX ON catalog USING hnsw (embedding vector_cosine_ops)` (or
`vector_ip_ops` once normalization is confirmed) is the pairing that
matches this workload: HNSW for the index type reasoned above, and an
operator class matching whichever of `<=>` or `<#>` the query itself uses,
since pgvector only accelerates a query whose `ORDER BY` operator matches
the index's operator class.

## 2. pg_trgm: indexing and similarity functions

### How it indexes text

Per the PostgreSQL documentation for `pg_trgm` (`postgresql.org/docs/current/pgtrgm.html`,
read 2026-09-20; "current" resolves to the PostgreSQL 18 documentation set
at the time of this read, but pg_trgm's indexing and function behavior is
part of the stable contrib module surface and this page's content was not
cross-checked against the PostgreSQL 16-specific docs tree, so any wording
difference between the 16 and 18 doc trees for this module is unverified):
a trigram is "a group of three consecutive characters taken from a string."
Non-alphanumeric characters are ignored when extracting trigrams, and each
word is padded with two spaces prefixed and one space suffixed before
extraction, so `"cat"` yields the trigrams `" c"`, `" ca"`, `"cat"`, `"at "`.
Both `gist_trgm_ops` (GiST) and `gin_trgm_ops` (GIN) operator classes index
the resulting trigram sets, and both accelerate the similarity operators
below plus `LIKE`/`ILIKE` and POSIX regular expression matching.

### GIN versus GiST for this workload

The pg_trgm page itself declines to compare GiST and GIN performance beyond
one sentence: "The choice between GiST and GIN indexing depends on the
relative performance characteristics of GiST and GIN, which are discussed
elsewhere." Assembling that "elsewhere" from the general index chapters
(`postgresql.org/docs/current/gin.html` and
`postgresql.org/docs/current/textsearch-indexes.html`, read 2026-09-20):

- **Lossiness.** For GiST-indexed text search, "a GiST index is lossy,
  meaning that the index might produce false matches, and it is necessary
  to check the actual table row to eliminate such false matches," and
  "lossiness causes performance degradation due to unnecessary fetches of
  table records that turn out to be false matches." GIN is described as not
  lossy for that same comparison. `gist_trgm_ops` specifically stores a
  fixed-length bitmap signature per value, default 12 bytes and tunable up
  to 2024 bytes via `siglen`; a longer signature is "a more precise search
  ... at the cost of a larger index." This is the pg_trgm page's own
  parameter, not the general text-search default of 124 bytes quoted for
  `tsvector` GiST indexes, which is a different, larger default for a
  different data type.
- **Build and update cost.** The GIN chapter states "insertion into a GIN
  index can be slow due to the likelihood of many keys being inserted for
  each item" and "build time for a GIN index is very sensitive to the
  `maintenance_work_mem` setting." No equivalent per-key insertion cost is
  documented for GiST, and the general text-search comparison states GiST
  build time is "not sensitive" to `maintenance_work_mem`, i.e. GiST is
  typically the cheaper index to build and to update, GIN the more
  expensive one, in exchange for GIN being lossless.
- **Query pattern.** The pg_trgm page has one specific, load-bearing
  statement for this choice: a `<->` distance query with `ORDER BY dist
  LIMIT n` "can be implemented quite efficiently by GiST indexes, but not
  by GIN indexes. It will usually beat the first formulation [a threshold
  query using `%`] when only a small number of the closest matches is
  wanted." GIN supports the `%`/`<%`/`<<%` threshold operators (returning a
  boolean, filtered then sorted) but not an index-accelerated nearest-match
  ordering.

For a catalog of a few thousand rows, built once and queried far more often
than updated (an entity-resolution catalog, not a write-heavy table), GIN's
documented downside (slower, more memory-sensitive build; more expensive
per-row insertion) costs very little in absolute terms at this row count,
while its documented upside (lossless, no heap recheck) means every match
GIN returns for a similarity threshold query is a real match, not a
candidate needing a second check. GiST becomes the better choice only if
the benchmark specifically wants an index-accelerated `ORDER BY t <-> query
LIMIT k` query, i.e. picking a small top-k by raw trigram distance rather
than filtering by a similarity threshold and sorting after. Given this
benchmark's shape (top-1 and top-5 accuracy: exactly a small top-k
retrieval), GiST's `<->` `ORDER BY ... LIMIT` path documented above is the
one that matches the query pattern, so **GiST with `gist_trgm_ops`** is the
better-supported choice for the trigram arm specifically because of how the
query will be run, not because GIN is worse in general; at this row count
either index type answers a query in a small fraction of a second
regardless, so the deciding factor is the documented query-pattern support,
not raw cost.

### `similarity`, `word_similarity`, and `strict_word_similarity`

All three are documented on the same pg_trgm page (read 2026-09-20):

- **`similarity(text, text)`**: a number from 0 to 1 based on counting
  trigrams shared between the two full, padded strings. It compares the
  whole of both arguments; a shorter or longer second string lowers the
  score even where every trigram of the shorter one matches somewhere in
  the longer one.
- **`word_similarity(text, text)`**: "returns a number that indicates the
  greatest similarity between the set of trigrams in the first string and
  any continuous extent of an ordered set of trigrams in the second
  string." It looks for the best-matching substring extent inside the
  second argument, without requiring that extent to fall on word
  boundaries. The doc's own worked example: `word_similarity('word', 'two
  words')` returns `0.8`, higher than `similarity('word', 'words')`'s
  `0.571429`, because word_similarity credits the best-matching extent
  rather than penalizing for the rest of the second string.
- **`strict_word_similarity(text, text)`**: "same as `word_similarity`, but
  forces extent boundaries to match word boundaries." Its worked example,
  `strict_word_similarity('word', 'two words')`, returns `0.571429`, the
  same as plain `similarity('word', 'words')` in that example, because
  forcing the match to a whole word (`words`, not an arbitrary substring
  extent) removes the credit word_similarity gave for a partial-word match.

### Recommendation for a roughly 20-character product description

A product description in this catalog (the map's own count: distinct
description length p50 21 characters, per #111) is close to a single
short phrase, not a long free-text field the query might be a substring of.
`word_similarity` and `strict_word_similarity` exist to find a query
matching a fragment of a much longer field (an address inside a paragraph,
one product mentioned in a sentence); their value comes from tolerating
extra text in the second argument that the query does not need to cover.
Where both the catalog description and the query are already the same kind
of short, whole phrase, whichever one plays the role of the "second string"
would have very little extra text for word_similarity's fragment-matching
behavior to earn credit for over plain `similarity`, and `strict_word_similarity`
additionally requires alignment to word boundaries, which adds a
constraint this comparison does not need since a mismatched word boundary
inside a 20-character phrase (a token split differently, a missing plural)
is exactly the kind of noise a whole-string trigram comparison already
absorbs. Plain **`similarity()`**, using the `%` operator against the
GiST-indexed `gist_trgm_ops` column (or the `<->` operator for the
`ORDER BY ... LIMIT` ranking pattern from the prior section), is the
documented fit here: whole-string comparison of two short strings of
comparable length, which is what `similarity()` is defined to do.

## 3. Reciprocal rank fusion over two ranked lists, in one SQL round trip

RRF combines rankings from independent retrieval methods without needing
their raw scores to be on comparable scales, by scoring each document only
by its rank position in each list: `RRFscore(d) = sum over rankings r of
1 / (k + rank_r(d))` (Cormack, Clarke, Büttcher, "Reciprocal Rank Fusion
outperforms Condorcet and individual Rank Learning Methods," SIGIR '09, read
2026-09-20 from the authors' own PDF at cormack.uwaterloo.ca).

### Worked query sketch

A trigram-ranked list and a vector-ranked list can each be produced as a
CTE using `row_number()`, then combined with a `FULL OUTER JOIN` on the
catalog row id so that a row present in only one arm still gets a partial
score, all in one statement:

```sql
WITH trgm_ranked AS (
    SELECT
        id,
        row_number() OVER (ORDER BY description <-> :query) AS rank
    FROM catalog
    ORDER BY description <-> :query
    LIMIT 50
),
vec_ranked AS (
    SELECT
        id,
        row_number() OVER (ORDER BY embedding <=> :query_embedding) AS rank
    FROM catalog
    ORDER BY embedding <=> :query_embedding
    LIMIT 50
)
SELECT
    coalesce(t.id, v.id) AS id,
    coalesce(1.0 / (:k + t.rank), 0.0)
        + coalesce(1.0 / (:k + v.rank), 0.0) AS rrf_score
FROM trgm_ranked t
FULL OUTER JOIN vec_ranked v ON t.id = v.id
ORDER BY rrf_score DESC
LIMIT 10;
```

Each CTE runs its own `ORDER BY ... LIMIT` against an index (`gist_trgm_ops`
for the trigram arm per section 2, `vector_cosine_ops` or `vector_ip_ops`
for the vector arm per section 1), Postgres executes both scans and the
join in one planned statement, and the outer query does the RRF arithmetic
and final ordering. This is a standard fusion pattern expressed against
pgvector's and pg_trgm's own operators; it is not itself a claim from
either project's documentation, only the two `ORDER BY` clauses and operator
choices inside it are.

### What the RRF constant does

The paper fixes `k = 60` for all its reported experiments ("where `k = 60`
was fixed during a pilot investigation and not altered during subsequent
validation") and states its purpose directly: "the constant `k` mitigates
the impact of high rankings by outlier systems." Without a constant, a
document ranked first by one arm and absent from the other would be
dominated entirely by that single rank-1 score (`1/1 = 1.0`); adding `k`
flattens the curve so a rank-1 result scores `1/(k+1)` rather than `1/1`,
narrowing the gap between a top rank in one arm and a moderate rank
supported by both arms. The paper's own pilot sweep (`k` from 0 to 500)
shows MAP moves only from 0.2072 at `k=0` to a peak of 0.2147 around
`k=80`, and the authors describe the choice as "near-optimal, but ...
not critical," i.e. RRF is documented as insensitive to the exact value of
`k`; 60 is a reasonable starting constant for this benchmark's fusion arm,
not a value that needs its own tuning pass.

## 4. Version facts

### Current pgvector release

The latest tagged release is **v0.8.6**, per the pgvector GitHub repository
(`github.com/pgvector/pgvector`, tags listing via the GitHub API, and the
raw `CHANGELOG.md` on the `master` branch, both read 2026-09-20). The
CHANGELOG lists `## 0.8.6 (2026-07-29)`; the GitHub API confirms the `v0.8.6`
tag points to a commit committed 2026-07-29T18:28:54Z, matching the
CHANGELOG date. The project has no formal GitHub "Releases" entries (the
releases page returns none), only signed tags with CHANGELOG entries; that
is the project's own release mechanism, not a gap in this research.

### What Ubuntu 24.04 ships

`postgresql-16-pgvector` in the Ubuntu 24.04 (noble) archive is **0.6.0-1**
(Launchpad's Ubuntu package listing for noble, read 2026-09-20), confirming
the version the ticket already reported from the local machine.

### What changed between 0.6.0 and 0.8.6 that matters here

From the CHANGELOG (raw `master` branch, read 2026-09-20), summarizing only
entries relevant to this benchmark:

- **0.7.0** added the `halfvec`, `sparsevec`, and indexable `bit` types;
  L1 (taxicab) distance support for HNSW; and the `binary_quantize`,
  `hamming_distance`, `jaccard_distance`, and `l2_normalize` functions. Not
  needed for a dense, few-thousand-row `vector` catalog, but `l2_normalize`
  is directly relevant if the benchmark ever needs to pre-normalize
  embeddings in SQL rather than in the embedding model or client code.
- **0.8.0** added iterative index scans (`hnsw.iterative_scan` /
  `ivfflat.iterative_scan`), which matters for any query that combines a
  vector `ORDER BY` with a `WHERE` filter, since filtering after an
  approximate index scan can otherwise return fewer than the requested
  number of rows; also "improved cost estimation for better index selection
  when filtering" and general HNSW scan and build performance improvements.
  This benchmark's rerank arm, if it ever filters the vector arm's
  candidates before reranking, is exactly the scenario iterative scans were
  built for.
- **0.8.1 through 0.8.6** are bug fixes and Postgres 18 compatibility work
  (parallel HNSW build buffer overflow, IVFFlat memory usage, HNSW
  vacuuming corruption, `EXPLAIN` output correctness); none change index
  behavior or add operators relevant to this benchmark's design.

0.6.0 itself already has parallel HNSW index builds and the storage/WAL
improvements from that release; the type system relevant here (`vector`,
`vector_cosine_ops`, `vector_ip_ops`, HNSW, IVFFlat) is present in 0.6.0
unchanged from what 0.8.6 offers. The two changes worth having, `l2_normalize`
and iterative scans, are both nice-to-have refinements rather than
blockers: an application can normalize embeddings in code instead of SQL,
and this catalog's row count (a few thousand rows) is small enough that a
non-iterative scan over-fetching or under-filtering by a few candidates is
unlikely to be the dominant source of noise in a benchmark this size. At
this scale, running on Ubuntu's shipped 0.6.0 rather than 0.8.6 is not a
blocker for the four-arm benchmark's correctness or for the core index and
operator choices in this file.

### What adding the PGDG apt repository would buy

The PostgreSQL Global Development Group's own apt repository documentation
(`wiki.postgresql.org/wiki/Apt`, read 2026-09-20) states it provides
"PostgreSQL server packages as well as extensions and modules packages on
several Debian/Ubuntu releases for all PostgreSQL versions supported," and
gives the Ubuntu 24.04 setup as `sudo apt install -y postgresql-common
ca-certificates && sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh`,
or manually adding a `noble-pgdg` suite entry pointing at
`https://apt.postgresql.org/pub/repos/apt`.

Fetching the PGDG `noble-pgdg` package index directly
(`apt.postgresql.org/pub/repos/apt/dists/noble-pgdg/main/binary-amd64/Packages`,
read 2026-09-20) confirms PGDG carries `postgresql-16-pgvector` at
**0.8.6-1.pgdg24.04+1**, matching pgvector's current upstream release
exactly, with 0.8.5-1.pgdg24.04+1 and 0.8.4-1.pgdg24.04+1 also present as
older entries in the same index. This is a direct, primary-source
confirmation that PGDG tracks upstream pgvector releases closely, while
Ubuntu's own `noble` archive is frozen at the 0.6.0 packaged at the Ubuntu
24.04 release and does not receive point updates through Ubuntu's regular
archive.

Given the version-gap analysis above, adding PGDG buys a genuinely current
pgvector build (0.8.6 instead of 0.6.0) with no functional gap on the
features this benchmark needs, at the cost of managing a second apt source
for the extension package on the local machine. Since the local catalog is
a few thousand rows, since 0.6.0 already carries the index types and
operators this file recommends, and since ticket #115 (not this one) owns
any actual change to the local cluster, this file records PGDG as available
and current, not as a recommendation to install; that decision belongs to
#115.

## 5. CI container image

### Which image to pin

The pgvector project publishes its own Docker image at `pgvector/pgvector`
on Docker Hub (read 2026-09-20). Its Dockerfile
(`github.com/pgvector/pgvector`, `Dockerfile` on the `master` branch, read
2026-09-20 via the GitHub API) shows the image is built `FROM
postgres:$PG_MAJOR-$DEBIAN_CODENAME`, i.e. it is the official Debian-based
`postgres` image with pgvector compiled in from
`ADD https://github.com/pgvector/pgvector.git#v0.8.6 /tmp/pgvector`, `make
install`, at whatever version tag the image build pinned. It therefore
inherits the official Postgres image's environment variable contract
(`POSTGRES_PASSWORD`, `POSTGRES_USER`, `POSTGRES_DB`) rather than defining
its own.

Docker Hub's tag listing for `pgvector/pgvector` (read 2026-09-20) shows,
among others, `pg16`, `pg16-bookworm`, `pg16-trixie`, `0.8.6-pg16`,
`0.8.6-pg16-bookworm`, and `0.8.6-pg16-trixie`. The bare `pg16` tag floats
to whatever pgvector version is current when the image is rebuilt; the
`0.8.6-pg16` form pins both the pgvector version and the Postgres major
version. For a CI job whose whole point is a reproducible fixture, the
fully pinned tag is the right one: **`pgvector/pgvector:0.8.6-pg16`** (or
`0.8.6-pg16-bookworm` for a pinned Debian base as well, if the base image's
own point releases ever need to be held fixed too). This matches the local
cluster's Postgres 16 major version while running a newer pgvector than the
Ubuntu-packaged 0.6.0, which section 4 already established costs nothing
functionally for this benchmark. Whether to prefer matching the local
0.6.0 exactly instead, for identical behavior between local and CI, is a
choice this file surfaces but does not make: the map (#111) already commits
to each row recording its own Postgres and pgvector version rather than
requiring the two to match, the same way a backend row records a served
model.

### GitHub Actions service container configuration

GitHub's own documentation for a Postgres service container
(`docs.github.com`, "Creating PostgreSQL service containers," read
2026-09-20) gives the shape a service container takes:

```yaml
services:
  postgres:
    image: postgres
    env:
      POSTGRES_PASSWORD: postgres
    options: >-
      --health-cmd pg_isready
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5
    ports:
      - 5432:5432
```

Since the pgvector image is a drop-in build of the official `postgres`
image (per the Dockerfile above), this same shape applies with the image
name swapped:

```yaml
services:
  postgres:
    image: pgvector/pgvector:0.8.6-pg16
    env:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: docmatch_test
    options: >-
      --health-cmd pg_isready
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5
    ports:
      - 5432:5432
```

`pg_isready` as the health check comes from the base `postgres` image's own
tooling, unchanged by pgvector's build; no pgvector-specific health check
is documented or needed, since the extension is compiled in rather than
loaded as a separate service.

## Synthesis: answering the four-arm question directly

1. **Vector arm.** `vector` type, an HNSW index, and either `<=>` with
   `vector_cosine_ops` (safe default, correct regardless of whether the
   chosen local embedding model's output is pre-normalized) or `<#>` with
   `vector_ip_ops` (cheaper per comparison, correct once that model's
   output is confirmed unit-normalized). HNSW over IVFFlat because this
   catalog's row count is too small for IVFFlat's `lists` heuristic to
   cluster meaningfully, and because HNSW's only documented cost, slower
   build, is negligible at a few thousand rows while its documented
   benefit, better query recall per unit of latency, is what this
   benchmark's latency-per-query number is measuring.
2. **Trigram arm.** `pg_trgm`'s trigram extraction indexed with
   `gist_trgm_ops` (GiST), because the pg_trgm documentation specifically
   credits GiST, not GIN, with efficient `ORDER BY column <-> query LIMIT
   k` execution, which is exactly this benchmark's top-1/top-5 retrieval
   shape. `similarity()` (whole-string comparison) is the right function
   for a roughly 20-character catalog description compared against a
   query of comparable length; `word_similarity()` and
   `strict_word_similarity()` exist to find a short match inside a much
   longer field, which this catalog does not have.
3. **Hybrid arm (RRF).** One SQL statement with two ranked CTEs (trigram
   distance, vector distance), each capped with its own `ORDER BY ...
   LIMIT`, combined by a full outer join and summed as `1/(k+rank)` per
   arm, ordered by the summed score. `k = 60`, the value fixed by Cormack,
   Clarke, and Büttcher's own pilot experiments, dampens how much a
   single arm's rank-1 result can dominate the fused score; the same
   paper's pilot sweep shows the result is not sensitive to the exact
   value chosen.
4. **Version verdict.** Current upstream pgvector is 0.8.6; Ubuntu 24.04's
   own archive ships 0.6.0. Everything this file recommends (the `vector`
   type, HNSW and IVFFlat, the distance operators, cosine and inner
   product operator classes) is present and unchanged in 0.6.0; the
   changes between the two versions that matter for this design
   (`l2_normalize`, iterative index scans) are refinements, not
   requirements, at this catalog's size. PGDG's `noble-pgdg` apt
   repository does carry a current `postgresql-16-pgvector` (0.8.6-1
   confirmed directly from its package index), so it is available if a
   later ticket wants it, but nothing in this research makes it necessary
   for the four-arm benchmark to work correctly. That installation
   decision belongs to ticket #115, not this one.
5. **CI container image.** `pgvector/pgvector:0.8.6-pg16`, the official
   pgvector-maintained image built from the same Dockerfile that pins the
   project's own release tag, configured as a standard GitHub Actions
   Postgres service container (`POSTGRES_PASSWORD`, optionally
   `POSTGRES_DB`, a `pg_isready` health check, port 5432), since the image
   is the official `postgres` image with pgvector compiled in and inherits
   its entire environment-variable and health-check contract unchanged.

## Sources

- pgvector README, `master` branch, read 2026-09-20:
  https://github.com/pgvector/pgvector/blob/master/README.md
- pgvector CHANGELOG, `master` branch, read 2026-09-20:
  https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md
- pgvector `Dockerfile`, `master` branch, read 2026-09-20 via GitHub API:
  https://github.com/pgvector/pgvector/blob/master/Dockerfile
- pgvector git tags and tag commit dates, read 2026-09-20 via the GitHub
  API (`repos/pgvector/pgvector/tags`,
  `repos/pgvector/pgvector/git/refs/tags/v0.8.6`,
  `repos/pgvector/pgvector/commits/{sha}`).
- PostgreSQL documentation, `pg_trgm`, "current" (PostgreSQL 18 doc tree at
  the time of this read), read 2026-09-20:
  https://www.postgresql.org/docs/current/pgtrgm.html
- PostgreSQL documentation, GIN indexes, "current," read 2026-09-20:
  https://www.postgresql.org/docs/current/gin.html
- PostgreSQL documentation, text search indexes (GIN/GiST comparison for
  `tsvector`), "current," read 2026-09-20:
  https://www.postgresql.org/docs/current/textsearch-indexes.html
- Cormack, G. V., Clarke, C. L. A., and Büttcher, S., "Reciprocal Rank
  Fusion outperforms Condorcet and individual Rank Learning Methods,"
  SIGIR '09, read 2026-09-20:
  http://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf
- Launchpad, Ubuntu noble package listing for `postgresql-16-pgvector`,
  read 2026-09-20:
  https://launchpad.net/ubuntu/noble/+package/postgresql-16-pgvector
- PostgreSQL Global Development Group, apt repository wiki page, read
  2026-09-20: https://wiki.postgresql.org/wiki/Apt
- PGDG apt archive package index for `noble-pgdg`, `binary-amd64`, read
  2026-09-20:
  https://apt.postgresql.org/pub/repos/apt/dists/noble-pgdg/main/binary-amd64/Packages
- Docker Hub, `pgvector/pgvector` repository overview and tags, read
  2026-09-20: https://hub.docker.com/r/pgvector/pgvector and
  https://hub.docker.com/r/pgvector/pgvector/tags
- GitHub documentation, "Creating PostgreSQL service containers," read
  2026-09-20:
  https://docs.github.com/en/actions/how-tos/use-cases-and-examples/using-containerized-services/creating-postgresql-service-containers
