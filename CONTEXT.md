# docmatch

A document reconciliation engine: invoices are extracted, validated, matched against purchase orders and receiving records, and every change is measured against a labeled benchmark.

## Language

### Benchmark documents

**Fixed subset**:
The committed list of documents every benchmark number is measured over, drawn from DocILE's val split.
_Avoid_: eval set, test set, sample

**Public copy**:
A document's PDF as published by its original archive (the UCSF Industry Documents Library), as opposed to the copy DocILE redistributes. Backends read public copies; labels come from DocILE.
_Avoid_: original, source PDF

**Admission**:
The check that a public copy has the same pages as DocILE's copy, so its labels apply to it. A document that fails admission is a reject and never enters the fixed subset.
_Avoid_: filtering, validation

### Extraction

**Derived value**:
A value code adds alongside a reading, taken from values that reading already carries, without looking at the page again or asking a model. It is kept apart from what the backend read, and it carries no confidence.
_Avoid_: inferred value, post-processed value

**Confidence**:
A backend's own score, returned alongside a value it read, for how likely that value is correct. Only some backends return one; a backend that does not has no confidence, never an assumed or model-graded one.
_Avoid_: certainty, probability, self-reported confidence

**Calibration**:
How well confidence predicts correctness: of the values a backend read at a given confidence, the share that are right. It speaks to precision only, since a value a backend missed carries no confidence.
_Avoid_: accuracy (on its own), reliability

**Gate**:
The deterministic rules a reading must not break: its totals agree and its dates are in order. A reading passes when at least one rule could be checked and none failed, fails when any rule failed, and is not checked when no rule could be. A reading that fails is kept and scored as is.
_Avoid_: validation (on its own), filter, guardrail

**Gate ablation**:
The comparison of what the gate and confidence each catch among readings whose gated values are wrong, so the gate is judged only on errors it could see.
_Avoid_: gate eval, gate accuracy

**Requested model**:
The model a run asks its backend to read with, one per run, written in the run's record. Each backend has a default; a model with no written price is refused.
_Avoid_: model (on its own), backend

**Served model**:
The model version a vendor reports actually reading a document, recorded on that document's row. It can differ from the requested model when a vendor points a name at a new version.
_Avoid_: actual model, resolved model

### Measurement

**Regression**:
A change that lowers field F1 or line-item F1 on the fixed subset below the benchmark row of a backend it touches. A backend's first row has nothing to regress from.
_Avoid_: degradation

**Cost per document**:
What reading one document costs at the vendor's list price for the units the vendor reported processing, tokens or pages, including attempts that failed. When a vendor that charges per page accepts an attempt but reports no pages, the pages sent are counted. Free tiers and what was actually billed play no part, so every backend's cost means the same thing.
_Avoid_: billed cost, spend, price per document

### Matching

**Seed**:
A labeled invoice that a purchase order and a receiving record are derived from, before any discrepancy is injected. The derived records copy each line as labeled; nothing missing from a line is filled in. A document with no labeled lines is never a seed.
_Avoid_: base invoice, template, source invoice

**Pairing**:
Deciding which invoice line answers which purchase-order line, one to one, by how closely their codes and descriptions agree, with quantities and prices only breaking ties, and breaking them by how close the values come rather than by their being equal. Receiving-record lines name their purchase-order line, so they need no pairing.
_Avoid_: line matching, alignment, reconciliation (on its own)

**Unpaired line**:
An invoice line or purchase-order line that pairing left without a partner. It is reported, never dropped: an unpaired invoice line is an extra line, an unpaired purchase-order line a missing line. A line that carries no code, description, quantity, price or amount, such as a row labeled with only a date, is not an item: pairing leaves it out and lists it as not compared, so it is never an unpaired line.
_Avoid_: orphan, unmatched line

**Pairing floor**:
The lowest similarity at which a code, or a description, counts as agreement in pairing; below it the two lines are no evidence of being the same item. Codes and descriptions each have their own. It is set from lines known to be unrelated, never from the readings the benchmark scores.
_Avoid_: threshold (on its own), cutoff, match score

**Pairing key**:
Which invoice line each purchase-order line answers, as the generator built the case. It is written when the case is built and read only when the case is scored, never by the matcher, so pairing stays the matcher's own job and a wrong partner is counted rather than hidden. It is partial: a purchase-order line a missing line added answers a line from another seed, and an invoice line that is an extra line answers none.
_Avoid_: line id, ground-truth pairing, link

**Discrepancy type**:
One way an invoice can disagree with its purchase order and receiving record: price variance, short-ship, over-ship, extra line, missing line, unit-of-measure variant, or tax mismatch. Only disagreement against docmatch's interest counts: billing less than was ordered or received is not a discrepancy. An invoice that repeats another invoice is not a discrepancy type; that is a separate control.
_Avoid_: exception, error, mismatch (on its own)

**Hold**:
The severity that stops an invoice from being approved automatically and sends it to review. Every discrepancy type holds except missing line.
_Avoid_: block, reject, fail

**Note**:
The severity that reports a discrepancy and leaves approval open. Only missing line is a note, since billing part of an order is normal.
_Avoid_: warning, info

**Tolerance**:
How far an invoice may go above its purchase order or receiving record before the matcher reports a discrepancy. A price or a tax amount may go over by a percentage of the purchase order's value, and always by a cent; a quantity may not go over at all; a unit of measure must read the same after the text normalization, since there is no conversion table. Only one set of tolerances exists at a time, and a finding's explanation quotes the one applied.
_Avoid_: threshold, allowance, variance limit

**Rounding drift**:
A difference of one cent between an invoice amount and its purchase order, always within the tolerance. It is never a discrepancy; reporting one counts against the matcher as a false positive.
_Avoid_: small difference, penny variance

**Case**:
One seed with the purchase order, receiving record and invoice derived from it, either clean or carrying one to three injected discrepancies, never two on the same line. Discrepancies are injected into the purchase order and receiving record; the invoice is never altered, so it stays the seed's labels or a backend's reading of the same document. Cases are rebuilt from the labels on every run and never saved.
_Avoid_: sample, example, test case

**Finding**:
A discrepancy type at a place: the purchase-order line it concerns, the invoice line for an extra line, or the header for a tax mismatch. A finding is right only when both its type and its place are; the right type on the wrong line is a miss and a false alarm.
_Avoid_: detection, flag, alert

**Match result**:
What matching returns for one case: its pairings with their scores, its findings, each value finding with the values compared and the tolerance applied and each unpaired line as an extra or missing line with the closest candidate and why it was not taken, and the comparisons it could not make with the reason. It is held when any finding is a hold, and approvable otherwise; approving is not matching's decision.
_Avoid_: match report, reconciliation, outcome

**Hard negative**:
A clean line built to tempt the matcher: rounding drift, a variance just inside the tolerance, or a price or quantity below the purchase order. Any finding on it is a false alarm.
_Avoid_: near miss, decoy

**End-to-end row**:
Matching measured with a backend's reading as the invoice, against the purchase order and receiving record derived from the same document's labels. Truth is always the generator's: a finding caused by a misread value is a false alarm, and an injected discrepancy the reading hides is a miss. A control row takes the labels as the invoice on the same cases, so the gap to it is what extraction costs.
_Avoid_: pipeline score, compound score
