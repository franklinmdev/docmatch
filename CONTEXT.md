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
