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

### Measurement

**Regression**:
A change that lowers field F1 or line-item F1 on the fixed subset below the benchmark row of a backend it touches. A backend's first row has nothing to regress from.
_Avoid_: degradation

**Cost per document**:
What reading one document costs at the vendor's list price for the units the vendor reported processing, tokens or pages, including attempts that failed. When the vendor accepts an attempt but reports nothing, the units sent are counted. Free tiers and what was actually billed play no part, so every backend's cost means the same thing.
_Avoid_: billed cost, spend, price per document
