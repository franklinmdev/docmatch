---
status: accepted
date: 2026-09-20
---

# The reranker keep-or-drop rule is written before the number

Phase 3 exits with the resolution table and a documented keep-or-drop decision
on the cross-encoder reranker. A decision made after seeing the number is a fit,
not a decision, so the rule is fixed here, before any arm has been measured
(#122 on the tracker, resolved the day this file was written), and the command
that measures the table applies it.

**The rule.** The reranker is kept when both hold, and dropped otherwise:

1. Its headline top-1 (over the scored answerable queries, exact weight
   `w = 2/3`, at the N the depth sweep chose) is at least **1.0 point** above
   the hybrid arm's.
2. Its p95 latency per query on the named machine is at most **500 ms**.

Top-1 is the only thing the reranker has to buy. The separability diagnostic is
printed beside the verdict and never weighs in it: the hybrid's fused score is
computed before the rerank either way, so keeping or dropping the reranker
neither gains nor loses that score's separability, and Phase 3 chooses no
threshold.

**Why one point.** The headline weights the exact third at `w = 2/3`, and every
arm answers an exact query, so the reranker can only move the noisy third: one
point of headline is three points over the 3,072 scored variants, about 92
queries net, roughly seven times the standard error of a paired difference at
that size. It is also the granularity the depth sweep already uses to choose
d and N. A paired significance test was rejected because at this n a gain of
0.3 points is already significant, which would keep a component that costs
about seven times the hybrid's latency for a gain with no operational weight.
Two points was rejected as a bar that would drop a real 1.5 point gain.

**Why 500 ms.** Resolving a document must never cost more than reading it: a
train document has 7.4 lines on average and the cheapest extraction row reads
one in 5.2 s at p50, so 500 ms per line is 3.7 s per document. The whole N grid
(10, 25, 50 pairs, about 47, 102 and 208 ms mean) fits under it, so the ceiling
only bites on a surprise, which is what a pre-registered ceiling is for. A
250 ms ceiling was rejected because it would likely bind at N = 50 and drop a
real top-1 gain by cost in a pipeline that is not interactive. No ceiling was
rejected because it leaves an unexpected p95 ungated.

**What the verdict does.** Kept means hybrid plus rerank is the arm Phase 4
resolves real lines with. Dropped means the rerank arm and its depth sweep are
deleted from `docmatch resolve` after the row is produced, the README row
links the commit that produced it, and Phase 4 resolves with the hybrid arm
(CLAUDE.md rule 7: prefer deleting a component). Keeping the arm as a
permanent ablation, or behind a flag off by default, was rejected as a path
nothing measures.

**Where it lives.** The two thresholds are constants in code beside d and N.
`docmatch resolve` prints the verdict with both measurements against both
constants, the way the pairing floor prints its constant beside the value the
procedure measures (rule 4: code decides). The README's Phase 3 section states
the rule, links this file and the commit whose run produced the verdict.
