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
Deciding which invoice line answers which purchase-order line, one to one, by how closely their codes and descriptions agree, with quantities, prices and the unit only breaking ties, and breaking them by how close the values come rather than by their being equal. Receiving-record lines name their purchase-order line, so they need no pairing.
_Avoid_: line matching, alignment, reconciliation (on its own)

**Unpaired line**:
An invoice line or purchase-order line that pairing left without a partner. It is reported, never dropped: an unpaired invoice line is an extra line, an unpaired purchase-order line a missing line. A line that carries no code, description, quantity, price or amount, such as a row labeled with only a date, is not an item: pairing leaves it out and lists it as not compared, so it is never an unpaired line.
_Avoid_: orphan, unmatched line

**Pairing floor**:
The lowest similarity at which a code, or a description, counts as agreement in pairing; below it the two lines are no evidence of being the same item. Codes and descriptions each have their own. It is set from lines known to be unrelated, never from the readings the benchmark scores.
_Avoid_: threshold (on its own), cutoff, match score

**Floor cost**:
What the pairing floors cost one backend's readings: of the reading lines the line-item metric pairs with a labeled line, how many the matcher's own pairing left without a partner because their best candidate agreed below a floor. Counted over one clean case per document, reported beside the floors and never tuned against.
_Avoid_: floor loss, left below a floor, unpaired rate

**Pairing key**:
Which invoice line each purchase-order line answers, as the generator built the case. It is written when the case is built and read only when the case is scored, never by the matcher, so pairing stays the matcher's own job and a wrong partner is counted rather than hidden. It is partial: a purchase-order line a missing line added answers a line from another seed, and an invoice line that is an extra line answers none.
_Avoid_: line id, ground-truth pairing, link

**Crossed pair**:
A line the matcher paired with a partner other than the one the pairing key names. It is no finding of its own: it shows up as a discrepancy on the line next door, or as nothing at all, so it is counted against the key to make what pairing cost visible before any rule ran. A crossing between two invoice lines alike on code, description, quantity, unit price and amount is counted apart, since nothing that decides a pair tells those two apart and which one the assignment takes is arbitrary.
_Avoid_: mispair, wrong match, swap

**Discrepancy type**:
One way an invoice can disagree with its purchase order and receiving record: price variance, short-ship, over-ship, extra line, missing line, unit variant, or tax mismatch. Unit variant is the short form of unit-of-measure variant, and the one the report and the README use. Only disagreement against docmatch's interest counts: billing less than was ordered or received is not a discrepancy. An invoice that repeats another invoice is not a discrepancy type; that is a separate control.
_Avoid_: exception, error, mismatch (on its own)

**Hold**:
The severity that stops an invoice from being approved automatically and sends it to review. Every discrepancy type holds except missing line.
_Avoid_: block, reject, fail

**Note**:
The severity that reports a discrepancy and leaves approval open. Only missing line is a note, since billing part of an order is normal.
_Avoid_: warning, info

**Tolerance**:
How far an invoice may go above its purchase order or receiving record before the matcher reports a discrepancy. A price or a tax amount may go over by a percentage of the purchase order's value, and always by a cent; a quantity may not go over at all; a unit of measure must read the same after the text normalization, since there is no conversion table. The margin is that percentage of the value compared against, in money, zero where the comparison is exact; a price or tax finding's explanation quotes it beside the tolerance applied, and an exact comparison says so instead. Only one set of tolerances exists at a time.
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
Matching measured with a backend's reading as the invoice, against the purchase order and receiving record derived from the same document's labels. Truth is always the generator's: a finding caused by a misread value is a false alarm, and an injected discrepancy the reading hides is a miss. The labels control takes the labels as the invoice on the same cases, so the gap to it is what extraction costs.
_Avoid_: pipeline score, compound score, control row

### Resolution

**Catalog entry**:
One thing resolution can name: a minted SKU and a canonical description, nothing more. The canonical description is a labeled description after the text normalization pairing already compares. The catalog is seeded from DocILE train alone: every normalized description that appears in two or more train documents is an entry, with no draw, no seed and no size parameter. Near-duplicates are kept apart, never collapsed, since two descriptions that differ only in their digits are two entries.
_Avoid_: product, item, master data

**SKU**:
The identifier a catalog entry is minted with: `SKU-` and the first eight hex characters of the SHA-256 of its canonical description. It depends on that description alone, so it never shifts when the catalog changes; a collision raises, and DocILE's own line codes play no part in it. It is the truth an answerable query is scored against.
_Avoid_: id, code (on its own)

**Query**:
One normalized description sent to an arm to be resolved, and nothing else: no code, no quantity, no price. It never carries two entries. Every query is either answerable or out of catalog.
_Avoid_: search, lookup

**Answerable query**:
A query generated from a catalog entry and carrying that entry's SKU as its truth. Every entry carries three, one exact query and two noisy variants.
_Avoid_: positive query, known query

**Out-of-catalog query**:
A query drawn from a train description that appears in one document only and whose nearest catalog entry sits below a pinned similarity, so it has no entry and carries no SKU. It is a fixed share of the set, it never enters the headline, and it exists for the separability diagnostic.
_Avoid_: negative query, distractor, unknown query

**Exact query**:
An entry's canonical description unchanged, one per entry. It is the reading a backend produces about two times in three, so it is carried once and weighted rather than repeated.
_Avoid_: clean query, gold query

**Noisy variant**:
A query made from an entry's canonical description by applying one noise kind at a calibrated strength, two per entry, each drawing its kind independently. A variant keeps its kind and is never a mix, the way a backend's error is a habit of a document rather than a rate per row.
_Avoid_: perturbation, augmentation, corrupted query

**Noise kind**:
One of the four ways a reading differs from its label that the noisy variants imitate, in the shares measured against the labels on the saved runs: extra words, a letter or two substituted, digits dropped, punctuation. Each kind's strength is calibrated so the variants' similarity to their entry reproduces the measured bands, and a test asserts both the shares and the bands. Case, whitespace, an empty reading, a split and a merge are not kinds.
_Avoid_: typo, corruption, error type

**Arm**:
One way to resolve a query: one indexed query against the catalog in Postgres plus a deterministic ordering applied in code, score then SKU ascending at every rank and at the cut, returning exactly five entries so top-5 means the same thing in every row. Three arms are measured: trigram only, vector only, and hybrid. A fourth, hybrid plus rerank, was measured once and deleted under the keep-or-drop rule.
_Avoid_: method, strategy, retriever

**Headline**:
An arm's top-1 or top-5 over the scored answerable queries, the exact queries and the noisy variants each scored on their own and combined at the exact weight. It is the number the README's resolution table shows; out-of-catalog queries never enter it.
_Avoid_: accuracy (on its own), score (on its own)

**Exact weight**:
The share of a headline the exact queries carry, pinned in code at the measured share of exact readings, about two in three, printed beside the table and the same for every arm. It carries the exact-reading rate as a weight rather than as duplicated queries, since an entry has one exact form.
_Avoid_: mixture rate, prior, exact ratio

**Development slice**:
The queries of the one fifth of entries that one pinned 80/20 split set aside for choosing knobs, the depth sweep among them, plus the same fifth of the out-of-catalog draw. No accuracy or latency number measured on it appears in the README; the depths it chose do. The catalog stays whole on both slices.
_Avoid_: validation set, dev set, tuning set

**Scored slice**:
The queries of the other four fifths of the entries and of the out-of-catalog draw, measured once, after every knob is set, to produce the table and the separability diagnostic. No knob is chosen on it.
_Avoid_: test set, holdout, evaluation set

**Separability diagnostic**:
Per arm, how well its top-1 score tells an answerable query from an out-of-catalog one: the share of out-of-catalog queries rejected at each of three cuts that keep a fixed high share of the answerable, plus the AUROC, the area under the receiver operating characteristic curve, with answerable positive. Each cut anchors to the arm's own answerable quantile, which is what makes the arms' incommensurable scores comparable. It chooses no operating threshold, that point is Phase 4's, and it was printed beside the keep-or-drop verdict and never weighed in it.
_Avoid_: threshold, cutoff, rejection rate

**Operating threshold**:
The hybrid arm's top-1 score below which a line resolves to no entry and carries no SKU. It is set on the development slice at the cut that keeps 0.95 of the answerable scores, lives in code, and every run prints the procedure's value beside it, the pairing-floor pattern. It only annotates: a line with no entry is shown to the reviewer with its score and is never a routing reason.
_Avoid_: threshold (on its own), cutoff, confidence

**Depth sweep**:
The procedure that sets the depth constant on the development slice: d, the rows each hybrid half keeps before fusion, over its grid on the hybrid arm. The rule picks the smallest value whose development top-5 is within one point of the grid's best, ties to the cheaper. The constant lives in code, and every run repeats the sweep and prints the procedure's value beside the constant, the pairing-floor pattern. A second sweep, N, the pairs sent to the reranker, ran by the same rule until the reranker was dropped.
_Avoid_: hyperparameter search, tuning, grid search

**Keep-or-drop rule**:
ADR 0001's rule on the reranker, fixed before any arm was measured: kept when its headline top-1 at the swept N clears the hybrid's by at least a pinned margin and its p95 latency per query on the named machine stays at or under a pinned ceiling, dropped otherwise. Top-1 alone decides. Kept would have made hybrid plus rerank the arm Phase 4 resolves with; dropped means the arm and its N sweep are deleted after the row lands. It was applied once: the verdict was dropped at `27bc70d`, the README records it, and the arm, its N sweep and the rule's two thresholds are gone from the command, so Phase 4 resolves with the hybrid.
_Avoid_: ablation (on its own), go/no-go, success criterion

### Pipeline

**Status**:
Where one uploaded document stands in the loop: received, extracted, validated, resolved, matched, needs review, approved or rejected. Each status between received and matched means the output that reaches it is saved, so work picks up from the last one and never reads the document twice; validated means the gate's result is saved, not that the reading passed. A document whose extraction fails goes from received straight to needs review, since there is no reading to go further with. Approved and rejected are final. The system approves a document with no routing reason; only a reviewer rejects, or approves a document in review.
_Avoid_: state (on its own), stage, step

**Routing reason**:
Why a document goes to review instead of being approved: its extraction failed, its gate failed, or its match result is held. Every reason a document has is attached when it is routed, once: after matching, or on receipt when extraction fails, so a reviewer sees them all together. A gate that checked nothing is not a reason. A reviewer's correction reruns what follows from it and shows the new result, and the document stays in review until the reviewer decides.
_Avoid_: exception, flag, review trigger

**Transition**:
One change of a document's status, kept for good: from and to, when, who made it (the system or a reviewer), and the routing reasons when it goes to review. A document's transitions are its history; nothing rewrites them.
_Avoid_: event, status change log, audit entry
