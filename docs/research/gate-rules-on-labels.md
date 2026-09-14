# Which gate rules DocILE's labels can support

Answers issue #19, part of the wayfinder map #15. The question: for each
candidate validation-gate rule, which DocILE fieldtypes it needs, what share
of documents carry them, and what share of the carrying documents pass the
rule on their own labels, over all trainval documents and over UCSF-sourced
documents only. A rule the labels themselves fail cannot be in the gate.

Counts, shares and fieldtype names only, no label text and no document ids, per
CLAUDE.md rule 6. The corpus is 5,680 trainval documents, 2,645 of them UCSF.

## Method

The survey reuses the engine's own loading and normalization code rather than
re-parsing anything: `docmatch.docile.dataset.DocileDataset` to read
annotations, `docmatch.metrics.fields.by_fieldtype` to group labels by
fieldtype, and `docmatch.metrics.normalization.normalize` to turn label text
into the same comparable form the scorers use. `metadata.source` is read
straight off the JSON, because `Annotation` does not model it.

A document "carries" a rule when every fieldtype it needs has at least one
label. A header fieldtype with more than one distinct normalized value is
counted separately as ambiguous rather than picked from arbitrarily, since the
rule would have to choose one to gate on. Three tolerances are tried wherever
arithmetic is involved: exact to the cent, within 1 cent, and within 0.5% of
the compared amount.

The script is `gate_rules.py`, alongside this file, and reads the dataset from
the absolute path `data/docile` in a checkout of this repository. It is
read-only and prints nothing but counts.

## Line amounts reconcile to the subtotal

Needs `line_item_amount_gross` (or `line_item_amount_net`) on every row, and
the matching `amount_total_gross` (or `amount_total_net`) on the header.

| Pairing | Docs carrying | Pass exact | Pass within 1 cent | Pass within 0.5% | Fail | Ambiguous total | Unreadable cell |
|---|---|---|---|---|---|---|---|
| gross sum -> amount_total_gross, all | 3,506 (61.7%) | 70.1% | 0.1% | 1.1% | 24.3% | 0.7% | 3.6% |
| gross sum -> amount_total_gross, ucsf | 1,699 (64.2%) | 70.8% | 0.3% | 2.3% | 22.5% | 0.2% | 3.9% |
| net sum -> amount_total_net, all | 385 (6.8%) | 80.3% | 0.3% | 0.5% | 14.3% | 0.8% | 3.9% |
| net sum -> amount_total_net, ucsf | 207 (7.8%) | 80.7% | 0.5% | 1.0% | 15.0% | 1.0% | 1.9% |

The gross pairing is the one worth gating on: it carries on most documents,
the net pairing barely applies. Even so, roughly a quarter of the documents
that carry it fail beyond 0.5%, and tolerance buys almost nothing: passes move
from 70% at exact to 71.9% cumulative at 0.5%, so the failures are not
rounding.

Failure modes, of the 853 (all) / 382 (ucsf) documents failing the gross
pairing beyond 0.5%:

| Category | All | UCSF |
|---|---|---|
| Uncategorized, amounts present and the sum genuinely differs | 786 (92.1%) | 339 (88.7%) |
| Some row lacks `line_item_amount_gross` | 58 (6.8%) | 34 (8.9%) |
| Line-level discount present (`line_item_discount_amount` or `line_item_discount_rate`) | 7 (0.8%) | 7 (1.8%) |
| Line currency differs from `currency_code_amount_due` | 3 (0.4%) | 3 (0.8%) |

Direction and size of the mismatch, over the same failing documents:

| | All | UCSF |
|---|---|---|
| Line sum under the total | 265 | 169 |
| Line sum over the total | 578 | 207 |
| Off by 20% or more | 437 | 271 |
| Off by 5% to 20% | 330 | 49 |
| Off within 5% | 76 | 56 |

Most failures are not near misses: over half of them are off by 20% or more,
and line sums run over the total more often than under it. That points at a
structural mismatch, the labeled line-item table not being the full set of
things the printed total adds up, rather than at discounts, multi-currency
rows, or rounding, none of which explain more than a few percent of the
failures between them.

## Tax arithmetic is consistent

Needs `amount_total_net`, `amount_total_tax` and `amount_total_gross` all on
the header, tested as net + tax = gross.

| | Docs carrying | Pass exact | Pass within 0.5% | Fail | Ambiguous |
|---|---|---|---|---|---|
| All | 329 (5.8%) | 67.2% | 2.1% (cumulative 69.3%) | 24.3% | 6.4% |
| UCSF | 188 (7.1%) | 76.1% | 3.7% (cumulative 79.8%) | 15.4% | 4.8% |

This triple is rare, under 8% of documents carry all three fields at once, so
a gate keyed on it would fire on a small minority of documents. Of the
failures, only a handful (7 of 80 on all documents) carry more than one
distinct `tax_detail_rate` value; the rest have no such record, consistent
with a single blended rate rather than a multi-rate breakdown the header
fields disagree with.

## Totals agree

Two shapes, since `amount_due` means different things depending on whether
`amount_paid` is also labeled.

**With `amount_paid` present**, tested as gross - paid = due:

| | Docs carrying | Pass exact | Pass within 0.5% | Fail | Ambiguous or unreadable |
|---|---|---|---|---|---|
| All | 372 (6.5%) | 44.1% | 44.7% cumulative | 46.2% | 9.1% |
| UCSF | 95 (3.6%) | 80.0% | 82.4% cumulative | 14.7% | 3.2% |

**Without `amount_paid`**, tested as gross = due:

| | Docs carrying | Pass exact | Pass within 0.5% | Fail | Ambiguous |
|---|---|---|---|---|---|
| All | 4,864 (85.6%) | 80.0% | 80.1% cumulative | 19.3% | 0.6% |
| UCSF | 2,307 (87.2%) | 93.5% | 93.9% cumulative | 5.6% | 0.5% |

The unpaid shape is the one that matters in practice: it carries on the large
majority of documents (85.6% all, 87.2% UCSF) and clears exact equality on
80% to 93.5% of them. The paid shape is rarer and much weaker on the full
corpus (44.1% exact) though it is strong on UCSF alone (80.0% exact) with a
sample five times smaller. Failure categories, of the 938 (all) / 129 (ucsf)
unpaid-shape failures beyond 0.5%:

| Category | All | UCSF |
|---|---|---|
| Uncategorized, gross and due present and genuinely differ | 902 (96.2%) | 126 (97.7%) |
| `amount_total_tax` also present (due may exclude or include tax differently than gross) | 36 (3.8%) | 3 (2.3%) |
| Multiple `currency_code_amount_due` values | 0 | 0 |

## Dates are sane

Needs `date_issue` and `date_due` both on the header, tested as issue <= due.

| | Docs carrying | Pass | Fail (issue > due) | Ambiguous | Unreadable, fell back to text |
|---|---|---|---|---|---|
| All | 688 (12.1%) | 94.6% | 1.3% | 1.2% | 2.9% |
| UCSF | 386 (14.6%) | 92.2% | 1.3% | 1.8% | 4.7% |

This is the strongest candidate by pass rate, but the weakest by coverage:
only about one document in eight carries both dates. Where it applies, the
labels almost never contradict it; the small "unreadable" share is the date
rule's own fallback to text (documents whose date text is not one calendar
day, covered in `normalization.py`), not a sign the rule is wrong.

## Identifier checksums

Which identifier fieldtypes exist in the corpus, and the share of their labels
that pass a checksum or a structural format check where one applies to that
fieldtype independent of country:

| Fieldtype | Docs (all) | Docs (ucsf) | Labels | Checkable | Pass |
|---|---|---|---|---|---|
| `iban` | 3 (0.1%) | 3 (0.1%) | 5 | 3 | 1 (33.3%) |
| `bic` | 27 (0.5%) | 27 (1.0%) | 31 | 31 | 27 (87.1%) |
| `vendor_tax_id` | 678 (11.9%) | 638 (24.1%) | 760 | 0 | no rule applies |
| `customer_tax_id` | 38 (0.7%) | 34 (1.3%) | 40 | 0 | no rule applies |
| `vendor_registration_id` | 47 (0.8%) | 44 (1.7%) | 52 | 0 | no rule applies |
| `customer_registration_id` | 10 (0.2%) | 4 (0.2%) | 10 | 0 | no rule applies |
| `account_num` | 112 (2.0%) | 111 (4.2%) | 135 | 0 | no rule applies |
| `bank_num` | 93 (1.6%) | 92 (3.5%) | 105 | 0 | no rule applies |

Only `iban` has a real checksum digit (ISO 7064 mod-97-10 over the rearranged
string). It applies to 3 documents in the whole corpus, too few to gate on,
and one of the three checkable labels fails it, so even that small sample does
not clear 100%. `bic` has no check digit, only a fixed shape (6 letters, 2
alphanumerics, optional 3 more); what is reported here is that structural
shape, not a checksum, and 87.1% of labels match it.

None of the other identifier fieldtypes carry a checksum that applies without
knowing the issuing country: VAT number checksums are per-country algorithms,
and DocILE's `vendor_tax_id` and `customer_tax_id` values are dominated by
formats (US EIN-like) that carry no check digit at all. `vendor_tax_id` is the
only one of these with meaningful coverage, 11.9% of all documents and 24.1%
of UCSF documents, but there is nothing checksum-shaped to test against it.

## What this means for the gate

- **Dates sane** (`date_issue <= date_due`) is the cleanest signal, 94%+ pass
  where both are labeled, but the labels only carry it on 12% to 15% of
  documents.
- **Totals agree without a paid amount** (`amount_total_gross == amount_due`)
  is the best combination of coverage and correctness: it carries on 85.6% of
  all documents and 87.2% of UCSF documents, and clears exact equality on 80%
  to 93.5% of them.
- **Line amounts reconcile to the subtotal**, on the gross pairing, carries
  almost as often (61.7% / 64.2%) but only clears exact equality on about 70%
  of carriers, and its failures are mostly large (20%+) mismatches rather than
  rounding, so the labels themselves do not support this rule at a tight
  tolerance.
- **Tax arithmetic** and **totals agree with a paid amount** both carry on
  under 8% of documents; neither has enough labeled coverage to justify a gate
  rule by itself.
- **Identifier checksums** apply to essentially no documents in this corpus:
  `iban` is the only fieldtype with a real checksum and it appears on 3
  documents total.

None of these numbers say a rule is wrong to keep in the pipeline as a
diagnostic. They say which rules the labels themselves can be trusted to
validate at gate time, at the tolerances tried here, and which would reject
labeled ground truth too often to serve as a hard gate.

## Reproducing this

```bash
uv run python docs/research/gate_rules.py
```

Run from a checkout with `data/docile` present (the script reads that
absolute path; adjust `DATA_ROOT` at the top of the file if the dataset lives
elsewhere). The script is read-only, prints counts only, and depends only on
the same `docmatch.docile` and `docmatch.metrics.normalization` code the
engine ships.
