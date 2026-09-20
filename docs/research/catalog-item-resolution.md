# How real procurement and AP systems resolve an invoice line to a catalog item

Research ticket #114, part of the Phase 3 map (#111). This grounds three choices for docmatch's synthetic catalog: its size, how many description variants one entry carries, and what share of queries should have no correct entry. It settles nothing else, and it proposes docmatch defaults only in the closing section, clearly separated from what the sources say.

All pages were read on 2026-09-20 unless a page's own date says otherwise. "Unverified" marks a claim no primary source in this pass confirmed. A claim from a vendor's blog, sales page, or an AP automation vendor's marketing copy is marked "vendor claim" and is not treated as evidence of accuracy, only as a data point about what vendors say. No content from `data/` or DocILE was read or used; this file is entirely about external, real-world systems.

## Summary

- Every ERP or procurement system read ties invoice-to-catalog matching to a **part number**, not to free-text description matching. SAP Ariba's default configuration matches on the supplier part number alone, exact and case-sensitive. NetSuite keys off its own internal item record, with an optional per-vendor `vendorCode` field. Dynamics 365 keys off its own item or product number, with an optional per-vendor "external item number." None of the primary sources read describe a fuzzy or similarity-based description match as the default path.
- UNSPSC is documented, by the UN Global Marketplace's own page, as a **classification of product and service categories**, used to classify a supplier's catalog offerings and procurement opportunities, not to identify one catalog line. It is a complementary tag, not a matching key.
- No vendor page or paper found in this pass **publishes a share of invoice lines expected to resolve to a catalog entry.** This figure is unverified everywhere I looked.
- Lines with no catalog or PO match are a **named, routine exception**, not a rejection. SAP Ariba raises an "Item Unmatched" exception and resolves it by manual match or by re-classifying the line to a charge type (tax, shipping, discount). Dynamics 365 requires "an item number or procurement category" for a line that was not on the PO, meaning a procurement category is an accepted substitute for an item match. Coupa maps a commodity/material group to a general ledger account. None of the four systems describe outright rejecting an unmatched line as the default behavior.
- **Real ERPs model description variance as one string per vendor, not many.** Dynamics 365's "external item description" record stores exactly one external item number, name, and description per product, per vendor (or per vendor group). The variance in how one catalog item is described comes from having multiple such vendor records, one each, not from many descriptions per vendor.
- The closest published accuracy numbers for item-level matching come from **e-commerce entity resolution benchmarks** (WDC Products, Amazon-Google, Walmart-Amazon), not from AP-specific research. Their shape differs from AP catalog resolution in an important way, detailed in section 5. State-of-the-art pairwise matchers reach F1 between about 0.64 and 0.89 on WDC Products depending on the variant's difficulty, and F1 in the mid-70s to mid-80s on the classic Amazon-Google and Walmart-Amazon benchmarks.

---

## 1. What identifies a catalog entry in practice

### SAP Ariba

Default catalog/contract matching in SAP Ariba's invoice reconciliation uses **only the supplier part number**, and the match is case-sensitive:

> "In the default configuration, the reconciliation process uses only the supplier part number to determine if a match exists between a material or service item on the invoice and a line-level contract, and matches to customer catalog items are case-sensitive. Your site can also be configured to use the line item reference number on the contract and the invoice for additional matching, or to use case-insensitive matching if the default case-sensitive matching on supplier part numbers doesn't succeed."

Source: SAP Help Portal, "Resolving an Item Unmatched Invoice Exception with Manual Match," Reconciling Invoices guide, version 2608, read 2026-09-20. https://help.sap.com/docs/buying-invoicing/reconciling-invoices/how-to-resolve-item-unmatched-invoice-exception-with-manual-match

So the precedence, as documented, is: **supplier part number** (default and primary), then optionally a **line item reference number**, then optionally **case-insensitive** matching on the same part number field. Description is not named as a matching key at all in this page. Separately, catalog items in SAP Ariba's CIF/cXML catalog format carry a Supplier Part ID, a Manufacturer Part ID, and a Supplier ID (per search-result summaries of SAP's Customer Catalog Format Reference and Ariba Catalog Solution documentation), but I could not read the primary catalog format page directly: the SAP Help Portal's catalog-format pages and its Customer Catalog Format Reference PDF did not yield readable text through the tools available in this pass, so the **exact required-versus-optional status of Manufacturer Part ID is unverified**.

### Microsoft Dynamics 365

Dynamics 365 Supply Chain Management documents several identifier layers on a product, in this stated structure:

- **Product number**: "the primary identifier for a product," global across legal entities.
- **Item number**: "the product identifier that a specific legal entity uses," ideally identical to the product number but can diverge per legal entity.
- **Product name and description**: explicitly called "the human-readable identifiers of a product," not unique ("you might find multiple products that have the same name").
- **External item number** (per customer or vendor): "you can maintain the item numbers, item names, and item descriptions that the customer or vendor uses. The references appear on external documents, such as sales orders, purchase orders, packing slips, and invoices." Stored per released product, per vendor (or vendor group), as three matched fields: external item number, description, and external item text.
- **GTIN/barcode**: maintained via GS1's Global Trade Item Number, per legal entity, per unit of measure (not a single global number in this system).
- **External codes**: per legal entity, per code type, used for statistical or tax codes, not searchable by default.

Source: Microsoft Learn, "Product identifiers," Supply Chain Management docs, page dated 2026-06-16, last updated 2026-09-01, read 2026-09-20. https://learn.microsoft.com/en-us/dynamics365/supply-chain/pim/product-identifiers

At invoice entry, a line that was not on the purchase order still needs a key: "You must select an item number or procurement category," confirming item number as the primary key and procurement category (not free-text description) as the fallback for non-item spend.

Source: Microsoft Learn, "Vendor invoices overview," Finance docs, page dated 2026-05-06, last updated 2026-09-01, read 2026-09-20. https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/vendor-invoices-overview

### NetSuite

NetSuite's own item record is the primary key (internal ID). The optional vendor-specific field is documented as:

> Field: `vendorCode`, type string, length 15, required: No, mapping: Basic / Vendor. Description: "Sets the vendor's item code."

This field is part of the Item Vendor List sublist, alongside `vendor` (the vendor record reference), `purchasePrice`, and `preferredVendor`.

Source: NetSuite Applications Suite, Record Browser, "Item Vendor List," Oracle NetSuite online help, read 2026-09-20. https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_N3712676.html

The Multiple Vendors feature must be enabled to record more than one vendor's code per item, and the field is optional, not required. I found no NetSuite documentation page in this pass that states whether vendor bill lines are matched to purchase order lines by item internal ID alone, by `vendorCode`, or with a description fallback; **the matching algorithm itself is unverified** from primary NetSuite documentation in this pass. A third-party blog (invoicedataextraction.com) claims `vendorCode` is the field that "everything from purchase order defaulting to vendor bill line matching depends on," but this is a blog, not NetSuite's own documentation, so it is not treated as verified here.

### Oracle Fusion Procurement

Search results over Oracle's own "What's New" pages describe **manufacturer and manufacturer part number** as an attribute that can be shown, linked, and selected alongside supplier item on requisitions and blanket purchase agreements, particularly when an item has more than one manufacturer relationship. I was not able to read a page in this pass that states an explicit precedence order (e.g. "supplier item first, then manufacturer part number, then description") for how Oracle Fusion resolves an invoice or requisition line to a master item. This precedence is **unverified** for Oracle Fusion.

### UNSPSC: classification, not identification

The UN Global Marketplace's own UNSPSC page describes UNSPSC as "a global classification system of products and services," used by suppliers to "classify the products and services of their company" and by buyers publishing "procurement opportunities." It groups categories into eight top-level segments. It does not identify one catalog line; it categorizes many.

Source: United Nations Global Marketplace, "United Nations Standard Products and Services Code (UNSPSC)," public page, read 2026-09-20. https://www.ungm.org/public/unspsc. No version or date is shown on this specific page.

Several non-primary sources (a procurement-software vendor's product-taxonomy guide, an invoicing SaaS vendor's help page) describe UNSPSC as a four-level, eight-digit hierarchy (segment, family, class, commodity, each two digits, with an optional two-digit business-function fifth level). I attempted to confirm this directly from GS1's own UNSPSC site and from a UNECE classification-guidelines PDF, and from UNGM's own help-center article on the topic; all three were blocked by a bot-detection wall in this pass and could not be read. **The exact digit structure of UNSPSC is therefore unverified against a primary source in this pass**, even though it is consistent with what UNGM's own page shows (categories aggregated into segments) and is widely repeated.

**Conclusion for point 1**: across every system read, the matching key is a **part number** (the buyer's own internal number first, a vendor-specific or supplier part number second), never a free-text description by default. UNSPSC and similar classifications sit alongside the part number as a category tag, not as the identifier that carries the match.

---

## 2. What share of invoice lines are expected to resolve to a catalog entry at all

**No primary source read in this pass publishes this figure.** I searched SAP, Oracle, Coupa, NetSuite, and Dynamics 365 documentation, plus AP-automation vendor documentation (Tipalti, Bill.com, Basware, Stampli, HighRadius, AppZen), and found no vendor technical documentation page and no paper stating a target or observed percentage of invoice lines that resolve to a catalog/item match, as distinct from a whole-invoice match rate.

APQC (American Productivity and Quality Center), an independent cross-industry benchmarking body, names "Percentage of invoice line items that are matched the first time" as one of its Open Standards Benchmarking measures for the accounts payable process, which confirms this is a real, tracked industry KPI. However, the page that would show the actual median or percentile figure is paywalled and returned a bot-verification wall in this pass, so **the number itself is unverified**.

Source (title only, figure unverified): APQC Open Standards Benchmarking, "Percentage of invoice line items that are matched the first time," accessed 2026-09-20, page blocked by a Cloudflare check before the figure could be read. https://www.apqc.org/resources/benchmarking/open-standards-benchmarking/measures/percentage-invoice-line-items-are

Several AP-automation vendors publish match-rate claims on their marketing pages (for example, a claim of "90% auto-matching" alongside "95% line-item capture," and separate claims of "95%+" and "98%+" match rates from AI-oriented vendors). These are **vendor claims**, not documented product behavior and not benchmark results, and they describe whole-invoice or line auto-matching against a PO in general (item plus quantity plus price agreement), not specifically "resolves to a catalog entry." They are not used as evidence here.

---

## 3. What happens to a line with no catalog entry

Every system read treats an unmatched line as a **routed exception**, not a hard rejection, and treats freight, tax, and other charges as a **separate category from catalog items**, not as catalog items that failed to match.

### SAP Ariba

An invoice line with no matching purchase order, contract, or (for services) approved service sheet line raises a named exception:

> "Item Unmatched: Your SAP Ariba solution is unable to find a matching contract, purchase order, or (for service invoices) approved service sheet line item for the invoice line item. Applies to: All documents."

Source: SAP Help Portal, "Invoice Exception Types Reference," Reconciling Invoices guide, version 2608, read 2026-09-20. https://help.sap.com/docs/buying-invoicing/reconciling-invoices/invoice-exception-types-reference

Resolution is either a manual match to a PO/contract line, or a re-classification to a **charge type** such as Sales Tax or Discount, which is a documented, separate procedure:

> "Matching an Exception to a Different Charge Type or PO/Contract Line... You manually match exceptions to charge categories such as tax or shipping charges in order to explain discrepancies associated with those charges."

Source: SAP Help Portal, "Matching an Exception to a Different Charge Type or PO/Contract Line," version 2608, read 2026-09-20. https://help.sap.com/docs/buying-invoicing/reconciling-invoices/how-to-manually-match-exception-to-different-charge-type-or-po-contract-line

Once matched (to an item, a PO/contract line, or a charge type), accounting information can be entered or split manually, including at the line level for non-PO invoices:

> "SAP-enabled sites can be configured to have company code support at the line level for non-PO invoices... you must enter company codes at the line item and split accounting level."

Source: SAP Help Portal, "Editing Accounting Information on Invoice Reconciliations," version 2608, read 2026-09-20. https://help.sap.com/docs/buying-invoicing/reconciling-invoices/how-to-edit-accounting-information-on-invoice-reconciliations

Nowhere in these pages does SAP Ariba describe automatically refusing or deleting an unmatched line; it is held as an exception until a human resolves it.

### Dynamics 365 Finance

A line added to a vendor invoice that "wasn't on the purchase order" requires "an item number or procurement category," meaning a non-item charge is coded to a **procurement category**, not refused, and is scoped out of item-level matching: "The line is included only in matching policies for invoice totals." The system also has a specific alert, "Invoice contains unallocated charges," raised when a line-level charge has not been assigned anywhere, which blocks submission to workflow until the user corrects it rather than silently accepting or rejecting the line.

Source: Microsoft Learn, "Vendor invoices overview," read 2026-09-20 (cited in section 1).

### Coupa

Coupa's chart of accounts derives a GL account from Material Group (Coupa's Commodity), which can be configured to map each commodity code to a single GL account, or to filter the list of available GL accounts for a given commodity:

> "Material Group also drives the available General Ledger values so we can map each Commodity code to a single GL Account or add the Material Group as an accounting segment to provide the user with a filtered list of GL Accounts for each Material Group."

Source: Coupa Compass, "Company Codes, Cost Centers, and GL Account Code Combinations from SAP to Coupa," SAP integration playbook, page revised 2019-02-08, read 2026-09-20. https://compass.coupa.com/en-us/products/total-spend-management-platform/integration-playbooks-and-resources/erp-integration-playbooks/sap-integration-playbook/company-codes-cost-centers-and-gl-account-code-combinations-from-sap-to-coupa

This page's revision date (2019) is old relative to the other sources here; I treat the commodity-to-GL-account mechanism as documented behavior but note it may not reflect the current Coupa UI. Coupa's own invoice FAQ confirms that a PO-referenced line inherits its accounting from the PO line, which implies non-catalog, non-PO lines need their GL coding assigned another way (by commodity, as above, or manually), though the FAQ page itself did not spell out the non-PO path in the text I could read.

Source: Coupa Compass, "Invoices FAQ," last edited 2025-09-01, read 2026-09-20. https://compass.coupa.com/en-us/products/product-documentation/supplier-resources/for-suppliers/coupa-supplier-portal/set-up-the-csp/invoices/invoices-faq

### How common are these lines

**No primary source read publishes a per-invoice frequency for freight, surcharge, tax, or one-off service lines specifically.** AP-automation vendors (Stampli, citing IOFM; Esker) publish a claim that non-PO invoices are 30 to 50 percent of *all invoices* a finance team receives, and a separate claim that "maverick spend" (off-catalog buying) can run from about 2 percent (cited for one large company with a strict no-PO-no-pay policy) up to 80 percent of invoices at companies with weak controls. These are **vendor claims about whole invoices, not about the share of lines within an invoice that are non-catalog charges**, and I did not independently verify IOFM's original figures from IOFM's own site. They are directionally useful (non-PO, non-catalog invoicing is common, not a rare edge case) but not a number this table can be built on.

**Conclusion for point 3**: a line with no catalog match is routed to a named exception (SAP), a procurement category (Dynamics), or a commodity-derived GL account (Coupa). None of the three refuse it outright. It is a first-class, expected path in every system read, not an error state.

---

## 4. How many description variants one catalog item typically carries, and what causes it

No AP vendor documentation publishes a count of description variants per catalog item. The strongest evidence found is structural, from how systems store vendor-specific descriptions, plus one quantitative cross-shop e-commerce benchmark whose shape differs from AP (flagged below).

### Structural evidence: one description per vendor, not many per vendor

Dynamics 365's "external item description" mechanism stores **exactly one** external item number, one name, and one item text per released product, per vendor (or per vendor group):

> "You can maintain the item numbers, item names, and item descriptions that the customer or vendor uses... You can associate the customer's or vendor's item number with a released product. You must make this association for each legal entity."

Source: Microsoft Learn, "Product identifiers," read 2026-09-20 (cited in section 1).

This means the ERP's own data model treats variance as coming from **how many different vendors supply the same catalog item**, each contributing one description, rather than from many descriptions per vendor. For a synthetic catalog, this points toward "variants per entry" being close to "vendors per entry," a small number (plausibly single digits for most items, since a given buyer typically sources a given catalog item from a handful of vendors, not dozens), rather than an open-ended count. I could not find a primary source that publishes a typical count of vendors-per-catalog-item, so the specific number is my own extrapolation from this structural fact, not a documented figure.

### What causes variation, per the closest matching-shaped benchmark

The WDC Products benchmark paper defines the kind of variance it targets directly, in terms that generalize past e-commerce:

> "In the context of product matching, a positive corner-case refers to a pair of matching product offers that exhibit dissimilarities in their surface forms, which are usually the result of heterogeneity introduced by different vendors, e.g. mentioning different product features in the offers or using different abbreviations or units of measurement."

Source: Peeters, Bizer, "WDC Products: A Multi-Dimensional Entity Matching Benchmark," accepted for EDBT 2024, submitted 2023-01-23 (arXiv:2301.09521), read 2026-09-20 via the ar5iv HTML mirror. https://arxiv.org/abs/2301.09521

This matches the causes docmatch would expect: different vendors describing the same physical item with different feature emphasis, different abbreviations, and different units of measurement, not random noise.

### The one quantitative figure available, with its shape flagged

WDC Products' own scale: 11,715 product offers describing 2,162 distinct products, extracted in 2020 from 3,259 different e-commerce websites using schema.org markup. That is an average of about **5.4 offers per product**, each offer from a different web shop.

Source: same WDC Products paper as above, section 4 "Benchmark Profiling," read 2026-09-20.

**Shape difference, stated explicitly**: this is 5.4 *listings of the same product across thousands of independent retail websites scraped from the open web*, each website's own copywriting. An AP catalog entry's variants come from a much smaller, closed set: the buyer's own vendors for that item, each typically describing it once, consistently, as shown by the Dynamics 365 structure above. The causes of variance (abbreviation, feature phrasing, unit-of-measure differences) transfer; the count does not, because the population generating the variants is structurally different (open retail web versus a buyer's own closed vendor list).

---

## 5. Published accuracy numbers for product or item matching

No AP-specific or procurement-specific accuracy benchmark was found. The closest published numbers come from academic entity-resolution and product-matching research, all pairwise or multi-class classification, not top-1/top-k retrieval against a catalog.

### WDC Products (Peeters and Bizer, EDBT 2024, arXiv:2301.09521)

Best pairwise-matching F1 (class "match") across the benchmark's 27 variants ranges from about **0.64 to 0.89**, worsening as the share of "corner-case" (textually dissimilar matches or textually similar non-matches) pairs rises and as the test set's entities become unseen in training:

> "The experiments confirm that the benchmark is challenging for state-of-the-art pair-wise matching systems which reach Top-F1 scores between 0.64 and 0.89 depending on the variant of the benchmark."

The best score I read directly in the results table (R-SupCon, large development set, 20 percent corner-cases, seen entities) was F1 = 89.04. The worst reported top-scoring configuration for the hardest, small/80-percent-corner-case/unseen split was F1 = 64.50 (RoBERTa). All systems' F1 dropped substantially when entities were unseen at test time versus seen during training, across every corner-case and development-size combination in the results tables.

Source: same paper as section 4, Table 3 and Table 4 (Results of the pair-wise experiments), read 2026-09-20.

### Amazon-Google and Walmart-Amazon (via the Ditto paper)

These are the classic "ER-Magellan" structured entity-matching benchmarks (originating with Mudgal et al., 2018, and used across a line of follow-up work). Reading the results table of a widely cited follow-up paper directly:

| Dataset | Prior best (DeepMatcher+) F1 | Ditto F1 | Pairs (n) |
|---|---|---|---|
| Amazon-Google (clean) | 70.7 | 75.58 | 11,460 |
| Walmart-Amazon (clean) | 73.6 | 86.76 | 10,242 |
| Walmart-Amazon (dirty, attributes shuffled) | 53.8 | 85.69 | 10,242 |

Source: Li, Li, Suhara, Doan, Tan, "Deep Entity Matching with Pre-Trained Language Models," arXiv:2004.00584, also published in Proceedings of the VLDB Endowment (VLDB 2021), Table 5, read 2026-09-20 via the ar5iv HTML mirror. https://arxiv.org/abs/2004.00584

The Amazon-Google dataset is domain-specific (largely software titles, per the paper's own domain table: "software / electronics"), and the Walmart-Amazon "positive rate," the share of candidate pairs in the dataset that are actual matches, is documented as 9.4 percent, meaning the benchmark is built from mostly-negative candidate pairs pre-filtered by a blocking step, not raw open retrieval.

### Why the shape differs from an AP catalog-resolution table

All three benchmarks above (WDC Products, Amazon-Google, Walmart-Amazon) are **pairwise binary classification**: given a candidate pair of records already pre-selected by a blocking or indexing step, decide match or non-match. None of them report top-1 or top-k retrieval accuracy over a full catalog the way docmatch's four-arm table (trigram, vector, hybrid, hybrid+rerank) does, where the system must find the correct entry among the whole catalog, not just judge one proposed pair. WDC Products does also offer a "multi-class" formulation (classify an offer as one of a fixed set of known products), which is closer in shape to top-1 retrieval, but I did not find its accuracy numbers reported separately from the pairwise numbers in the sections read.

The domains also differ: these benchmarks match **retail product offers across independent public sellers** (a many-to-many, open-world matching problem across thousands of websites). docmatch's Phase 3 problem is matching **one buyer's invoice line text to that buyer's own closed internal catalog** (a many-to-one, closed-world lookup against a catalog the buyer controls and whose entries a vendor's invoice wording drifts around, per section 1's evidence that ERPs expect a stable per-vendor part number or description, not open-web-style heterogeneity). The F1 numbers above should be read as an upper-difficulty reference point for how hard *pairwise* text matching between heterogeneous descriptions of the same product can get, not as a number docmatch's top-1/top-5 retrieval table should be expected to match or beat.

---

## What this grounds

Three numbers for docmatch's synthetic catalog, each rated by how solid its evidence is.

### 1. Catalog size

**No primary source publishes a target or typical AP catalog size**, and none of the benchmarks read are AP catalogs; they are e-commerce product-offer corpora. The only quantitative anchors available are benchmark scales built for a similar-shaped matching problem: WDC Products uses 2,162 distinct products; the classic Amazon-Google and Walmart-Amazon benchmarks work with roughly 1,300 to 3,200 distinct source-side entities. These are **weak evidence for docmatch's catalog size**, since they were sized for research convenience and for exercising the corner-case dimensions those papers study, not for representing a realistic AP catalog.

**This is the weakest-grounded of the three numbers.** My recommendation, stated as extrapolation rather than as a documented fact: size the synthetic catalog in the low thousands of distinct entries, similar in order of magnitude to WDC Products and the Magellan benchmarks (roughly 1,000 to 3,000 entries), large enough that trigram-only and vector-only retrieval have room to diverge, but not so large that the benchmark becomes a scale exercise rather than an accuracy one. This is a design choice informed by benchmark precedent, not a number any AP source states.

### 2. Variants per catalog entry

**Better grounded, but still not a documented figure.** The structural evidence (Dynamics 365's one-description-per-vendor data model, section 4) supports variants-per-entry tracking roughly with vendors-per-entry, which for a typical purchased item is a small number. The one quantitative cross-shop benchmark (WDC Products, about 5.4 offers per product) lands in a similar small range despite its different, more open-world shape (thousands of independent retailers, not a buyer's own vendor list).

**Recommendation**: 2 to 6 description variants per catalog entry, weighted toward the low end (most entries with 2 to 3 vendor-style variants, a smaller share with more), varied by the same kinds of drift the WDC Products paper documents: different feature phrasing, different abbreviations, different units of measurement, applied per vendor. This is a best-guess range anchored on the Dynamics 365 structural fact (solid, though not numeric) and cross-checked loosely against the WDC Products figure (numeric, but different shape); it is not read directly off any AP-specific source.

### 3. Share of queries with no correct catalog entry

**The weakest-grounded number, by a clear margin.** Every system read treats out-of-catalog lines (freight, tax, surcharges, one-off services) as routine and expected, not rare, but no source quantifies how many lines on a typical invoice they are, and no source quantifies what share of *queries* (as opposed to whole invoices) should have no answer. The 30-to-50-percent non-PO-invoice figure and the wide maverick-spend range (2 to 80 percent) are vendor claims about whole invoices, not about lines within an invoice, and are not carried into this recommendation as a number.

**Recommendation**: treat this as a deliberately chosen design parameter, not a fitted one. A modest minority share, in the region of 10 to 20 percent of queries with no correct catalog entry, is consistent with the qualitative picture across every source (out-of-catalog lines are common enough that every system names them as a distinct, handled case, but they are not shown anywhere as the majority of lines on a typical catalog-backed invoice). This is explicitly a guess bounded by the shape of the evidence, not a value taken from any measurement.

### What was not verified

- A published share of invoice lines expected to resolve to a catalog entry (section 2): not found anywhere in this pass.
- The exact digit structure of UNSPSC (segment/family/class/commodity, two digits each): consistent with secondary sources, but the primary UN/GS1/UNECE pages that would confirm it were blocked by bot-detection in this pass.
- SAP Ariba's CIF/cXML catalog format's required-versus-optional fields (Supplier Part ID, Manufacturer Part ID): the primary catalog-format reference page and PDF did not yield readable text with the tools available.
- Oracle Fusion Procurement's precedence order for invoice-to-item matching: no page read stated an explicit order.
- NetSuite's actual vendor-bill-to-PO matching algorithm (whether `vendorCode` participates, whether description is ever a fallback): no NetSuite documentation page read described the matching logic itself, only the fields that exist.
- Any per-invoice frequency for freight, surcharge, tax, or one-off service lines: not found.
- WDC Products' multi-class (closer to top-1) accuracy numbers, as distinct from its pairwise numbers: not read in the sections fetched.
