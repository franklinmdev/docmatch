# Alternatives considered

A decision record: which problem domains were evaluated for docmatch, why this one was chosen, and what evidence would justify a pivot.

## Selection criteria

1. Labeled data exists or can be derived exactly, so evals have ground truth instead of opinions.
2. The core check is deterministic once the documents are structured, so correctness is measurable and explainable.
3. The engineering goes beyond a single model call: validation, retrieval, matching, review, measurement.
4. The problem is legible to a general engineering audience without domain lectures.
5. There is a path to regional adaptation through adapters rather than forks.

## Candidates

| Domain | Verdict | Note |
|---|---|---|
| Invoice three-way matching | **chosen** | Real line-item data exists; matching is deterministic; discrepancy labels can be generated exactly. |
| Commercial lease and contract clause extraction | runner-up | See below. |
| Bank statement to ledger reconciliation | runner-up | See below. |
| Customs and freight document cross-check | rejected | Tariff classification dominates; labeled shipping documents are private. |
| Healthcare claims pre-authorization | rejected | Clinical data is regulated and unavailable; error cost is unacceptable for a solo build. |
| Request-for-quote bid comparison | rejected | Normalizing technical specifications needs industrial catalogs; little beyond table extraction. |
| Employee expense policy auditing | rejected | Single-page documents leave little room beyond extraction; the industry is moving control upstream to cards. |
| Procedure manuals versus audit logs | rejected | Semantic gap between prose rules and machine logs produces unmanageable false positives. |
| Support ticket to database patch generation | rejected | Generating mutations against production data is an anti-pattern. |
| Vendor due diligence synthesis | rejected | Value lives in paid data sources; without them it degrades into summarization. |

## Two eliminations that rested on weak arguments

Both runners-up were eliminated in an earlier evaluation for reasons that do not hold. They remain the most likely successors or replacements.

### Contract clause extraction

The elimination argument was that no ground truth exists without a legal team labeling clauses. That is wrong: the Contract Understanding Atticus Dataset provides 510 commercial contracts with 41 clause types labeled by lawyers, which is exactly the eval set the argument said could not exist.

What still makes it second: the core task is retrieval and classification rather than reconciliation, so it demonstrates a narrower slice of the pipeline, and the regional adaptation path is weaker.

When it would replace docmatch: if extraction accuracy on invoices saturates early with cheap models and the matching layer proves trivial, the harder retrieval problem in long documents becomes the better showcase. The harness, tracing, and review loop transfer directly.

### Bank reconciliation

The elimination argument was that most bank reconciliation is deterministic once the data is structured, so it is not an AI problem. The same is true of three-way matching. The argument eliminates the chosen project with equal force, so it was not a real argument.

What still makes it second: bank statements carry less structure than invoices, so the extraction and entity-resolution problems are thinner, and public labeled statement data is scarcer than labeled invoices.

When it would replace docmatch: if entity resolution turns out to be the most interesting layer, transaction-to-ledger matching is a richer version of the same problem and can reuse the retrieval stack unchanged.

## Pivot triggers

Any of these, observed in the Benchmarks section, opens a pivot discussion recorded here:

- Extraction field F1 above 0.97 on the fixed subset with the cheapest backend and no gate. The extraction layer would demonstrate little.
- Matching precision and recall above 0.99 on every discrepancy type at first attempt. The matching layer would demonstrate little.
- Entity resolution is the only layer where methods differ materially. Bank reconciliation becomes attractive.
- The DocILE terms prevent publishing the numbers needed for the README. Contracts with CUAD become attractive.
