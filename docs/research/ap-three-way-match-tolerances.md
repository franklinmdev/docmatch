# How real AP systems define three-way match tolerances and discrepancy types

Research ticket #64. Feeds the Phase 2 taxonomy, severities, and default tolerances (#65, #66). This file records what vendors do. It does not propose docmatch defaults; that decision belongs to a later ticket.

All pages were read on 2026-09-18 unless a stated page date says otherwise. "Unverified" marks a claim the vendor's own documentation did not confirm in the pages read. Nothing under `data/` or from DocILE was used.

## Summary

- Every vendor separates **quantity** checks from **price** checks. Percentage and absolute limits both exist, but not in every place.
- **Only Dynamics 365 ships a stated default** (legal entity price tolerance of 0 percent). SAP, Oracle Fusion, and NetSuite document no shipped numeric defaults. SAP and NetSuite treat "not configured" as a behavior (SAP DW blocks, NetSuite skips the check). Oracle treats an active tolerance with no value as infinite variance.
- Exceeding a limit **holds or blocks the invoice for payment**. It does not reject it. All four systems let a human release or approve.
- Tax and unit of measure (UoM) are handled much less uniformly than price and quantity. Oracle and Dynamics have explicit tax tolerance concepts. SAP and NetSuite do not document a tax tolerance in the pages read.
- Duplicate detection is a separate control from matching in all four systems.

---

## 1. SAP S/4HANA (Logistics Invoice Verification, MIRO)

### Tolerance kinds

Tolerance limits are set per **tolerance key**, per **company code** (Customizing path Materials Management, Logistics Invoice Verification, Invoice Block, Set Tolerance Limits, transaction OMR6; the Fiori configuration app is "Tolerance Limits for Invoice Postings"). Each key can carry up to four limits: lower and upper, each absolute and/or percentage. The set of limits allowed differs per key.

Keys documented on the S/4HANA Help page (version 2025 FPS01, Feb 2026), with the limit types the page's table allows:

| Key | What the page says it checks | Limits allowed |
|---|---|---|
| AN | Amount of an item without order reference, against an absolute upper limit (when the item amount check is active) | upper absolute |
| AP | Amount of an item with order reference, against an absolute upper limit | upper absolute |
| BD | "Form small differences automatically": invoice balance against an absolute upper limit; within it the system posts the difference to an Expense/Income from Small Differences line | upper absolute |
| DQ | "Exceed amount: quantity variance": net order price times (quantity invoiced minus open quantity), compared with absolute limits; optional percentage limits on quantity variance | lower/upper, absolute and percentage |
| DW | Quantity variance when goods receipt quantity is zero (GR defined but not yet posted) | upper absolute |
| PP | Price variance: variance of each item from quantity invoiced times order price | lower/upper, absolute and percentage |
| ST | Date variance: amount times (scheduled delivery date minus invoice entry date), against an absolute upper limit | upper absolute |
| VP | Moving average price variance after the posting | percentage only |

The Help page lists only these eight keys. Other keys exist in practice (BR, BW, KW, LA, LD, PS). The SAP Learning lesson "Entering Invoices with Variances" groups them as: BR and BW as order price quantity variances, KW, PP and PS as price variances, LA and LD as blanket purchase order limits, AN and AP as amount checks, BD as small differences. The per-key formulas for BR, BW, KW, LA, LD, PS were **not confirmed on a SAP Help page** in this pass: unverified. The SAP ERP 6.0 EHP2 "Setting Tolerances" page confirms the underlying concepts: price variance (with special tolerances when the order price is flagged as an estimated price), quantity variance (separately for invoice after goods receipt and before goods receipt), order price quantity variance (same split), and schedule variance.

Supplier-specific limits: tolerance groups are defined per company code (transaction OMRX) and assigned in the vendor master. They cover total-based acceptance: negative and positive "small difference" limits, plus absolute and percentage lower/upper limits for accepting a balance. The system accepts a difference if it is under the small difference limit, or under the **lower** of the absolute and percentage limits. If no group is assigned, or the group has no limits, the BD limit applies.

### Defaults

- **No shipped numeric defaults are stated** in the S/4HANA pages read. Limits must be configured per company code: unverified whether the system ships example values.
- Behavior when not configured: "If you have not maintained tolerance key DW for your company code, the system blocks an invoice for which no goods receipt has been posted yet." Setting all limits of a key to "Do not check" accepts any variance ("does not make sense" for BD, per the page).
- The ERP 6.0 EHP2 page shows example values (absolute 20.00 and 25 percent lower, absolute 10.00 and 10 percent upper) as an illustration of the four-limit screen. They are illustrative, not documented defaults.
- To block on a variance in all cases, the ERP page says to set the upper value and percentage to zero and select "Check limit".

### Consequence of a breach

A variance beyond a limit gives a warning. If an **upper** limit (except BD and VP) is exceeded, the invoice is posted but **blocked for payment**, and a separate release step is needed. If the **BD** limit is breached, "the system cannot post the invoice". The block applies to the whole invoice even if one item varies. Other block reasons: item amount (AN, AP), stochastic (random, per company code, with a threshold value and percentage), manual.

### Discrepancy types (variance categories)

SAP's "Invoice with Variances" page names four: **quantity variance**, **price variance**, **quantity and price variance**, and **order price quantity variance** (order price unit versus order unit ratio differs from goods receipt). The tolerance key list above is the operational taxonomy.

### Unit of measure

Handled as **order price quantity variance**. When the PO price refers to a unit different from the order quantity unit (example: ordered in pieces, priced per kg), the system computes the ratio of order price quantity to order quantity. It compares the invoice ratio with the goods receipt ratio, or with the PO ratio if the invoice arrives before the goods. A different ratio is a variance (example on the page: 2.5 kg per piece on the PO, 2.4 at receipt, 2.6 on the invoice). This is a unit-ratio check, not general UoM conversion. How SAP treats an invoice entered in a wholly different UoM than the PO line is unverified.

### Tax

Tax amounts are calculated by the system from item amounts and tax codes, and tax codes are proposed from the PO item. SAP Learning says that if the supplier invoice differs, "you must change the entries for tax codes and tax amounts according to the supplier invoice during invoice entry". So tax differences versus the PO are corrected at entry, not tolerated by a tax tolerance key. The invoice must balance: net item amounts plus tax plus planned delivery costs against the invoice amount. Any residue is governed by the BD small-differences limit and total-based acceptance. No tax-specific tolerance key appears in the documented list.

### Duplicates

"Check for Duplication of Invoice Entry" (S/4HANA Cloud Public Edition 2608): enabled per supplier with the **Check Double Invoice** indicator on the business partner. Always compared: **supplier, currency, gross invoice amount**. Optionally added via the configuration activity "Set Check for Duplicate Invoices": **company code, reference, invoice date**, and (per the Cloud page) credit memo. The result is a warning or an error depending on settings. In Logistics Invoice Verification the check only runs if a reference document number was entered. It compares Financial Accounting documents first, then Logistics Invoice Verification documents that contain errors or were entered for background verification.

### Sources (SAP)

- Tolerance Limits for Invoice Postings, SAP S/4HANA on-premise Help, version 2025 FPS01 (Feb 2026), read 2026-09-18: https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/b7eb2f9e70ab4c88abbff8b34a409b26/ec236b54f94c8f4ce10000000a4450e5.html
- Supplier-Specific Tolerance Limits for Invoice Postings, 2025 FPS01 (Feb 2026), read 2026-09-18: https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/b7eb2f9e70ab4c88abbff8b34a409b26/224c6f54bbea8d4ce10000000a4450e5.html
- Invoice with Variances, 2025 FPS01 (Feb 2026), read 2026-09-18: https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/af9ef57f504840d2b81be8667206d485/4270b6531de6b64ce10000000a174cb4.html
- Blocking Invoices, 2025 FPS01 (Feb 2026), read 2026-09-18: https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/ed84b70c199d4470ae2e5ccb93b2e45b/7870b6531de6b64ce10000000a174cb4.html
- Setting Tolerances, SAP ERP 6.0 EHP2 (out of maintenance), read 2026-09-18: https://help.sap.com/docs/SAP_ERP/ffc393c91a904eb5b0bec93aa34e42d8/8770b6531de6b64ce10000000a174cb4.html
- Check for Duplication of Invoice Entry, SAP S/4HANA Cloud Public Edition 2608, read 2026-09-18: https://help.sap.com/docs/SAP_S4HANA_CLOUD/0e602d466b99490187fcbb30d1dc897c/a971b6531de6b64ce10000000a174cb4.html
- SAP Learning, Invoice Verification in SAP S/4HANA: Entering Invoices with Variances (no page date), read 2026-09-18: https://learning.sap.com/courses/invoice-verification-in-sap-s-4hana/entering-invoices-with-variances-1
- SAP Learning, Handling of Variances Without Reference to an Item (no page date), read 2026-09-18: https://learning.sap.com/courses/invoice-verification-in-sap-s-4hana/handling-of-variances-without-reference-to-an-item
- SAP Learning, Use Further Blocking Reasons (no page date), read 2026-09-18: https://learning.sap.com/courses/invoice-verification-in-sap-s-4hana/use-further-blocking-reasons
- SAP Learning, Applying Taxes (no page date), read 2026-09-18: https://learning.sap.com/courses/invoice-verification-in-sap-s-4hana/applying-taxes

Note: the help.sap.com pages are rendered client side. They were read through a headless browser, so the text is the vendor's own, but the SAP Learning pages were read through a summarizing fetcher and quotes there are as returned by that tool.

---

## 2. Oracle Fusion Cloud Payables (and EBS 12.1 hold table)

### Tolerance kinds

Tolerances are **tolerance templates** assigned to a **supplier site**. If a supplier has no templates, validation uses the templates on the Manage Invoice Options page. Two families by match basis (Oracle Fusion Cloud Financials, Using Payables Invoice to Pay, 25D, document G40916-01):

Quantity-based (invoices matched by Quantity):
- Ordered Percentage, Maximum Ordered (quantity over ordered)
- Received Percentage, Maximum Received (quantity over received)
- Price Percentage (percentage over PO schedule unit price)
- Conversion Rate Amount (variance between invoice and PO schedule amount in ledger currency, for foreign currency)
- Schedule Amount (variance between all invoice amounts in entered currency and the PO schedule amount)
- Total Amount (Conversion Rate Amount and Schedule Amount combined)
- Consumed Percentage, Maximum Consumed (consumption advice quantity)

Amount-based (invoices matched by Amount):
- Ordered Percentage, Maximum Ordered, Received Percentage, Maximum Received, Conversion Rate Amount, Total Amount (all on amounts)

So Oracle has percentage and absolute forms for ordered and received quantity or amount, a percentage for price, and amount limits for currency and schedule variance. Tolerances only apply to the upside ("more than"). Freight and miscellaneous lines are not supported in the tolerance setup.

Price corrections: validation uses a weighted average unit price over the corrected invoice and its price corrections.

Tax tolerance is separate, defined at configuration owner tax options: a maximum tax override amount and a maximum percentage deviation from the calculated tax. "The lower of the two values is considered" (Fusion Tax documentation, 25D).

### Defaults

- **No shipped numeric defaults are documented** in the pages read. Values are set per template.
- Semantics of empty and zero (Oracle, Invoice Tolerances): "If you specify a percentage tolerance of zero, variance isn't allowed. ... If an active tolerance doesn't have a value, then infinite variance is allowed."

### Hold and discrepancy types

Fusion 25D "Types of Holds" groups holds into Account, Funds, Invoice, **Matching**, and **Variance** hold reasons. Matching and Variance hold reasons are system-defined. Only Invoice Hold Reason and Invoice Line Reason allow user-defined holds. Holds block payment until released. Each hold in the table also shows whether accounting is allowed and whether manual release is allowed.

Matching holds (Fusion 25D, and the same list with fuller reasons in the EBS 12.1 Oracle Payables User's Guide):

| Hold | Cause (as documented) |
|---|---|
| Qty Ord | Quantity billed more than quantity ordered times (1 plus % tolerance) |
| Max Qty Ord | Quantity billed more than quantity ordered plus the tolerance amount |
| Qty Rec | Quantity billed more than quantity received times (1 plus % tolerance) |
| Max Qty Rec | Quantity billed more than quantity received plus the tolerance amount |
| Price | Weighted average price of matched invoice distributions and price corrections more than PO unit price times (1 plus % tolerance) |
| AMT ORD, AMT REC | Amount billed more than amount ordered (or received) times (1 plus % tolerance) |
| MAX AMT ORD, MAX AMT REC | Amount billed more than amount ordered (or received) plus the maximum tolerance amount |
| Max Rate Amount | Exchange rate variance exceeds amount tolerance |
| Max Ship Amount | Variance between invoice and shipment amount exceeds amount tolerance |
| Max Total Amount | Sum of exchange rate and shipment amount variance exceeds tolerance |
| MILESTONE | Quantity or amount billed not equal to total quantity or amount ordered |
| Quality | Quantity billed more than quantity accepted |
| Rec Exception | PO receipt indicates an exception |
| Final Matching | Invoice matched to a PO that another invoice already final-matched |
| Matching Required | Supplier site holds unmatched invoices and the invoice is not matched |
| Can't Close PO, Can't Try Final Close | PO close conditions |
| Tax Difference | Invoice tax code value not equal to the PO tax code, or equal but taxable flag set to No for PO shipments |

Variance holds: **Dist Variance** (invoice amount not equal to sum of distributions), **Prepaid Amount**, **Tax Amount Range** (tax outside rate times taxable amount plus or minus a tolerance amount), **Tax Variance** (tax outside rate times taxable amount times (1 plus or minus % tolerance)).

Invoice holds relevant to matching: **Amount** (invoice amount above the supplier site's invoice amount limit), **Invalid PO** (no valid PO number for matching).

The Fusion validation process page says it checks "variances between ordered, received, consumed, and invoiced quantities or amounts" and places holds. Its own examples: billed quantity exceeds received quantity, invoice price exceeds PO schedule price.

Note the asymmetry in Oracle's formulas: all matching holds trigger only when billed is **more than** the baseline. A short invoice (billed under received) creates no hold in these formulas. Under-billing is not a documented matching hold.

### Unit of measure

Fusion: no statement on cross-UoM conversion during matching was found in the pages read (unverified). EBS 12.1 (Oracle Payables User's Guide): "In the Match to Purchase Orders and Match to Purchase Order Distributions windows, the Quantity Invoiced must be in the same unit of measure as the purchase order shipment. In the Match to Receipts ... the Quantity Invoiced must be in the same unit of measure as the receipt." Fusion invoice lines carry Quantity, Unit Price, and UOM (with the third derived from any two).

### Tax

Explicit and separate: the **Tax Difference** matching hold (invoice tax code differs from the PO tax code), and the **Tax Variance** and **Tax Amount Range** variance holds (calculated tax versus invoice tax, percentage and amount tolerances). Tax overrides have their own tolerance (max amount and max percentage, the lower applies). Whether the Fusion tax page ships default tolerance values is not stated (unverified).

### Duplicates

- The invoice import documentation states invoice numbers must be unique for the supplier and "the import process rejects invoices with duplicate invoice numbers" (25D PDF).
- Additional duplicate check (Fusion 25D FAQ): "When the duplicate invoice check feature is enabled, it performs checks based on the combination of the supplier, invoice type, amount, currency, and date." It is enabled with lookup type `ADD_DUPLICATE_INV_CHECK`, lookup code `DUPLICATE_INVOICE_CHECK`.
- The 25D PDF also lists a hold named "Duplicate invoice matching required" among holds excluded from account coding workflow; its cause and release were not described in the pages read (unverified).
- The exact key of the standard (always-on) check in the UI (business unit, supplier, supplier site, invoice number) appears only in a secondary blog result and is **unverified against primary source**.

### Sources (Oracle)

- Oracle Fusion Cloud Financials, Using Payables Invoice to Pay, 25D (G40916-01, PDF, 584 pages), read 2026-09-18. Sections used: Invoice Tolerances, How PO Quantity Tolerance Is Validated, How Invoice Price Corrections and Tolerances Are Calculated, Types of Holds, invoice import validation, FAQ on duplicate invoices and price corrections: https://docs.oracle.com/en/cloud/saas/financials/25d/fappp/using-payables-invoice-to-pay.pdf
- Invoice Tolerances (web page, release stated only in the URL as 25d), read 2026-09-18: https://docs.oracle.com/en/cloud/saas/financials/25d/fappp/invoice-tolerances.html
- Invoice Holds and Releases (web page, 25d), read 2026-09-18: https://docs.oracle.com/en/cloud/saas/financials/25d/fappp/invoice-holds-and-releases.html
- How Invoices Are Validated (25d), read 2026-09-18: https://docs.oracle.com/en/cloud/saas/financials/25d/fappp/how-invoices-are-validated.html
- Tax Overrides and Tolerances on Payables Transactions (Fusion Tax, 25d), read 2026-09-18: https://docs.oracle.com/en/cloud/saas/financials/25d/fautx/tax-overrides-and-tolerances-on-payables-transactions.html
- How can I find duplicate invoices without considering the invoice number as the only check (26a), read 2026-09-18: https://docs.oracle.com/en/cloud/saas/financials/26a/fappp/how-can-i-find-duplicate-invoices-without-considering-the.html
- Oracle Payables User's Guide, Release 12.1, Oracle Payables Holds (E12797), read 2026-09-18: https://docs.oracle.com/cd/E18727_01/doc.121/e12797/T295436T367250.htm
- Oracle Payables User's Guide, Release 12.1, matching windows (UoM statement), read 2026-09-18: https://docs.oracle.com/cd/E18727_01/doc.121/e12797/T295436T366808.htm

---

## 3. NetSuite (3 Way Match Vendor Bill Approval and bill variances)

NetSuite has two separate mechanisms: an **approval workflow with tolerance and difference limits** (a SuiteApp), and **bill variance posting** to accounts. The help pages carry no product version or date.

### Tolerance kinds

The **3 Way Match Vendor Bill Approval Workflow** (in the NetSuite Approvals Workflow SuiteApp) validates a vendor bill against its purchase order and item receipt. Six fields, set on the **item**, **vendor** (Financial tab), and **subsidiary** (Vendor Bill Matching tab) records:

| Field | Meaning |
|---|---|
| Vendor Bill to Purchase Order Quantity Tolerance | percentage limit on quantity discrepancy |
| Vendor Bill to Purchase Order Amount Tolerance | percentage limit on amount discrepancy |
| Vendor Bill to Purchase Order Quantity Difference | absolute quantity limit |
| Vendor Bill to Item Receipt Quantity Tolerance | percentage limit |
| Vendor Bill to Item Receipt Amount Tolerance | percentage limit (listed in the setup tables) |
| Vendor Bill to Item Receipt Quantity Difference | absolute quantity limit |

Definitions: a tolerance limit "determines what percentage of the evaluated value is used as the limit"; a difference limit "represents an absolute quantity number". Worked example on the workflow page: PO amount 1000, bill 970, quantity 100 versus 97, amount tolerance 0.10, quantity difference 2. The bill is routed for approval because the quantity discrepancy exceeds its limit, while the amount discrepancy alone would auto-approve.

The Item Receipt Amount Tolerance field is documented in the setup tables, but the exception criteria list has no criterion that uses it (only `VB-IR` quantity criteria appear): treat its effect as unverified. There is no price tolerance and no absolute amount difference limit in this workflow. Price is compared through the amount. The exception criteria list contains no unit price or tax criterion.

### Defaults

- **No shipped default.** Fields are blank: "If you leave the fields blank on the subsidiary and vendor record, the workflow doesn't execute the criteria that use them." Entered values must be positive numbers greater than zero (decimals allowed).
- The plain "greater than" and "less than" criteria (`[SS] VB Qty Greater Than PO Qty`, `VB Qty Less Than PO Qty`, `VB Amt Greater Than PO Amt`, `VB Amt Less Than PO Amt`) fire on any difference, so they must be disabled (in a copy of the workflow) when using tolerances. The best-practices page shows that leaving one enabled routes a bill for approval even when it is inside the difference limit (100 ordered, 98 received, 98 billed, difference limit 4 still routed).
- Restriction: "Item receipts that have been partially received are not supported."
- Which of item, vendor, or subsidiary limit wins when several are set is **not specified** on the pages read (unverified). Each level has its own exception criterion.

### Named exception criteria (the discrepancy taxonomy)

By workflow state:
- Bill Validation: `VB-PO: Terms`, `Receipt of Item` (no item receipt exists for the bill), `VB-PO: Location`, `VB Standalone` (bill created without a PO).
- Quantity Tolerance Validation: `VB-PO` and `VB-IR` quantity tolerance, each at Vendor, Item, Subsidiary level.
- Quantity Difference Validation: `VB-PO` and `VB-IR` quantity difference at three levels, plus `VB Qty Greater Than PO Qty` and `VB Qty Less Than PO Qty`.
- Amount Validation: `VB-PO` amount tolerance at three levels, plus `VB Amt Greater Than PO Amt` and `VB Amt Less Than PO Amt`.

A bill with any exception goes to Pending Approval and is locked until the supervisor approves or rejects; with none it is auto-approved. States: Approval Routing, Bill Validation, Quantity Tolerance, Quantity Difference, Amount Validation, Pending Approval, Rejected, Approved, Show Exceptions.

### Bill variances (accounting side)

Where a PO line has **Match Bill to Receipt** checked (defaultable on the item), the Post Vendor Bill Variances process compares bill lines with receipt lines and posts journals for three variance types: **Bill Quantity Variance**, **Bill Price Variance**, **Bill Exchange Rate Variance**, to accounts set on the item. Journal: debit Accrued Purchases, credit the three variance accounts. Once posted, the PO, receipts, and bills "can't be changed" until the journal is voided or deleted. The pages read state no tolerance threshold for variance posting. Drop-ship lines should not use Match Bill to Receipt. The formulas quoted in a search summary (Bill Price Variance and Bill Quantity Variance) were not on the fetched page and are unverified.

### Unit of measure

No matching-specific UoM rule was found (unverified). Related facts on the multiple-units pages: conversion rate is "the quantity of base units that equal one unit of the current line"; a base unit is locked at 1; purchase orders default to purchase units and invoices to sales units; one transaction line cannot exceed 9,999,999,999 base units. How the approval workflow compares quantities across differing units is unverified.

### Tax

No tax tolerance or tax exception criterion was found in the workflow docs (the criteria list has none). The vendor bill page mentions only that a taxable discount is applied before tax. Treat tax matching as **no vendor equivalent found** for NetSuite.

### Duplicates

Vendor bill entry: "If you enter a reference number that is a duplicate for the vendor, a warning may display when you attempt to save the vendor bill." The user can continue or re-enter. It is a warning, keyed on the vendor and the reference number (the exact key and whether it is configurable are unverified beyond that statement).

### Sources (NetSuite, none show a version or date; read 2026-09-18)

- 3 Way Match Vendor Bill Approval Workflow: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_4096219721.html
- 3 Way Match Vendor Bill Approval States: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_4096454192.html
- 3 Way Match Vendor Bill Approval Exception Criteria: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_4096471509.html
- Setting Tolerance and Difference Limits: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_4168980481.html
- Best Practices When Using the Tolerance and Difference Limits: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_4212033610.html
- Entering Vendor Bill Matching Details: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_1504284393.html
- Vendor Bill Variances: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_N2371184.html
- Vendor Bill Variance Journals: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_N2373098.html
- Entering a Vendor Bill: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/article_161968486146.html
- Setting Up Units of Measure: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_N2212143.html
- Multiple Units of Measure: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/chapter_N2211898.html

---

## 4. Microsoft Dynamics 365 Finance (accounts payable invoice matching)

### Tolerance kinds

Matching is configured per legal entity on Accounts payable parameters, with overrides. Four validation families (Set up Accounts payable invoice matching validation, ms.date 2025-08-04):

1. **Line level matching**, policy **Not required**, **Two-way**, or **Three-way**. Two-way compares invoice net unit price with PO net unit price. Three-way adds invoice quantity versus matched product receipt quantity. The policy can be overridden by item and vendor, item, vendor, or per PO line (hierarchy: item and vendor, item, vendor, legal entity).
2. **Invoice totals matching**: six totals compared (balance or subtotal, total discount, charges, **sales tax**, round-off, invoice amount) against expected totals derived from PO prices, charges, tax, and invoice quantities. One percentage tolerance at legal entity level, overridable per vendor.
3. **Charges matching**: per charges code, percentage tolerance on the Charges tolerances page (only for codes flagged "Compare purchase order and invoice values").
4. **Price totals for line item matching**: for multiple invoices against one PO line, a not-to-exceed check on cumulative net amount (unit price times quantity plus charges minus discounts), as **percentage**, **amount**, or **percentage and amount**. With both, either exceeded is a discrepancy. Percentage compares in transaction currency, amount in accounting currency.

Price tolerance for net unit price: percentage only, on the Price tolerances page, with scope search order Table/Table, Table/Group, Table/All, Group/Table, Group/Group, Group/All, All/Table, All/Group, All/All (item scope by vendor scope). Icons can flag positive discrepancies only or positive and negative ("If greater than or less than tolerance"). "By default, negative price discrepancies are allowed."

**Quantity**: three-way matching compares invoice quantity to product receipt quantity; a difference is a "quantity matching error" (Product receipt quantity match column). The matching pages read show **no quantity tolerance percentage** for this check: unverified whether one exists elsewhere. Over-delivery and under-delivery percentages exist on the order line ("purchase order overdelivery percentage: the percentage by which product receipt quantities are allowed to exceed the purchase order quantity", AX 2012 glossary, archived). The current-release PO line behavior was not read directly (unverified); the current pages found for over-delivery percentage concern sales and warehouse packing slips (error SYS24920, ms.date 2021-05-31) and landed cost over/under transactions (amount tolerance on the whole order, then percentage tolerance per line, ms.date 2026-06-22), which are receiving controls, not AP matching.

Per-line price fields compared on the Invoice matching details page: unit price, price unit, charges, discount, discount percent, multiline discount, multiline discount percentage, net amount, net unit price.

### Defaults

- **Stated default**: "The legal entity default price tolerance is set to zero percent for two-way and three-way matching", applied to all items and all accounts (All, All) and not deletable.
- Invoice totals tolerance percentage, price totals tolerance, and charges tolerance: entered by the user, **no shipped value stated** (unverified).
- Worked examples on the pages use 5, 8, 10, 15, and 20 percent, 2 percent net unit price, and 100.00 or 500.00 amounts. These are examples, not defaults.

### Discrepancy types

Dynamics names them as **match statuses per compared field** with a Passed or Failed status and variance percentage, rather than a separate list of holds: net unit price and price total (price), invoice totals, charges, and quantity versus product receipt. Posting behavior is controlled by "Post invoice with discrepancies" (Allow with warning, or Require approval). "Approve posting with matching discrepancies" is a toggle on Invoice matching details. Failed lines show icons on the vendor invoice page. Related submission blocks in workflow: invoice total not equal to registered total, unallocated charges, duplicate invoice number, invoice quantity less than matched product receipt quantity (each behind a feature parameter).

Lines on the invoice with no PO line: "The line is included only in matching policies for invoice totals" (vendor invoices overview, ms.date 2026-05-06).

### Unit of measure

Not part of the documented matching policies in the pages read (unverified for core matching). The **Invoice capture** solution has a parameter "Validate unit of measure for PO invoice" that "ensure[s] the consistency of the unit of measure between the invoice line and its associated purchase order line" (ms.date 2026-07-28). Matching compares net unit price, so UoM consistency is enforced upstream, not tolerated.

### Tax

Sales tax is matched as one of the six **invoice totals** (example: expected 137.50 versus actual 139.98, variance 2 percent, Passed under a 20 percent tolerance). The sales tax article says a total difference "can cause a matching discrepancy" if invoice totals matching is enabled, and can be approved if the parameter is Require approval. The user can then change the tax group, or override the calculated tax with the correct amount. Sales tax codes that differ from the PO are also to be reviewed and corrected or the invoice put on hold. Invoice capture has "Validate total sales tax amount" and "Validate total amount" parameters. So tax is tolerated only through the invoice-totals percentage, not a separate tax tolerance.

### Duplicates

Parameter "Check the invoice number used" set to **Reject duplicate** blocks submission to workflow when the invoice number equals a posted invoice's number (vendor invoices overview, ms.date 2026-05-06). Other values of that parameter and whether the key includes the vendor are not stated on the page read (unverified). Invoice number length is 20 characters, extendable to 50 from version 10.0.40.

### Sources (Microsoft Learn, ms.date as stated on each page, read 2026-09-18)

- Accounts payable invoice matching overview, ms.date 2025-05-15: https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/accounts-payable-invoice-matching
- Set up Accounts payable invoice matching validation, ms.date 2025-08-04: https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/tasks/set-up-accounts-payable-invoice-matching-validation
- Three-way matching policies, ms.date 2026-06-04: https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/three-way-matching-policies
- Invoice matching and intercompany purchase orders, ms.date 2026-07-28: https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/invoice-matching-intercompany-purchase-orders
- Vendor invoices overview, ms.date 2026-05-06: https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/vendor-invoices-overview
- Resolve sales tax differences, ms.date 2026-07-31: https://learn.microsoft.com/en-us/dynamics365/finance/general-ledger/resolve-sales-tax-differences-purchase-orders-invoices
- Configure the Invoice capture solution, ms.date 2026-07-28: https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/config-invoice-capture
- Process over/under transactions (landed cost, receiving control), ms.date 2026-06-22: https://learn.microsoft.com/en-us/dynamics365/supply-chain/landed-cost/over-under-transactions
- Quantity exceeds over-delivery percentage during packing slip generation (sales side, error SYS24920), ms.date 2021-05-31: https://learn.microsoft.com/en-us/troubleshoot/dynamics-365/supply-chain/warehousing/quantity-exceeds-over-pack-slip
- purchase order overdelivery percentage (AX 2012 glossary, archived, ms.date 2014-08-25): https://learn.microsoft.com/en-us/previous-versions/dynamicsax-2012/appuser-itpro/purchase-order-overdelivery-percentage

---

## 5. Cross-vendor comparison

### Tolerance kinds

| Kind | SAP | Oracle Fusion | NetSuite | Dynamics 365 |
|---|---|---|---|---|
| Price, percentage | PP (lower and upper), KW/PS (see caveat) | Price Percentage (upper only) | none (price only via amount) | Price tolerances page, net unit price |
| Price, absolute | PP absolute | Schedule Amount, Total Amount | none | Purchase price total tolerance (amount, accounting currency) |
| Quantity, percentage | DQ percentage | Ordered / Received / Consumed Percentage | PO and receipt Quantity Tolerance | none found in AP matching |
| Quantity, absolute | DQ absolute (value-weighted), DW | Maximum Ordered / Received / Consumed | PO and receipt Quantity Difference | none found in AP matching |
| Over-receipt | via DQ, DW | via Received tolerances | via receipt tolerances | PO line overdelivery percentage (receiving) |
| Total / amount | AN, AP (item amount blocks), BD (balance) | Amount-based tolerances, Total Amount | PO and receipt Amount Tolerance (percent) | Invoice totals percentage, charges percentage |
| Small-difference auto-accept | BD, supplier total-based acceptance | none found | none found | round-off is one of six totals |
| Date | ST | none found | none found | none found |

### Defaults

| System | Shipped default stated? |
|---|---|
| SAP | No numeric default stated. DW unset means block. |
| Oracle Fusion | No numeric default stated. Zero percent means no variance. Active tolerance without value means infinite variance. |
| NetSuite | No default. Blank means the criterion is not run. Values must be greater than zero. |
| Dynamics 365 | Yes for one: legal entity price tolerance of 0 percent. Others not stated. |

### Mapping docmatch's planned discrepancy types

| docmatch type | Nearest vendor concept | Fit |
|---|---|---|
| Price variance | SAP PP; Oracle Price hold; Dynamics net unit price and price totals; NetSuite Bill Price Variance (accounting) | Clean. Percentage is universal; absolute form exists in SAP, Dynamics, and Oracle amount tolerances. |
| Short-ship (received less than ordered) | SAP DQ and DW (invoice versus open quantity); Oracle Qty Rec; Dynamics product receipt quantity match; NetSuite VB-IR quantity tolerance and `VB Qty Less Than PO Qty` | Vendors detect it as invoice versus receipt or PO quantity mismatch, not as a "short-ship" category. Oracle's holds fire only when billed exceeds the baseline, so a short-ship invoice is silent there. |
| Over-ship (received more than ordered) | Oracle Qty Ord, Max Qty Ord; SAP DQ; NetSuite `VB Qty Greater Than PO Qty`; Dynamics PO line overdelivery percentage | Clean for Oracle and NetSuite. Dynamics AP matching side unverified. |
| Missing line | none found as a named type | No vendor equivalent found. SAP presents open quantity per PO item; Oracle "Matching Required" and NetSuite `Receipt of Item` are header-level. |
| Extra line | Dynamics: invoice line not on PO is "included only in matching policies for invoice totals"; SAP AN (item without order reference); NetSuite `VB Standalone` (whole bill without PO) | Partial. SAP and Dynamics handle a non-PO line; Oracle "Invalid PO" is header-level. |
| UoM variant | SAP order price quantity variance (ratio check); Oracle EBS same-UoM requirement; Dynamics invoice capture UoM validation | Partial. Only SAP has a tolerance-style ratio check. The others require consistency. Cross-UoM conversion during matching is unverified in all four. |
| Tax mismatch | Oracle Tax Difference, Tax Variance, Tax Amount Range; Dynamics invoice totals sales tax and tax override | Clean for Oracle (three named holds, percentage and amount tolerances). Dynamics via totals. SAP and NetSuite: no tax tolerance found. |
| Duplicate invoice | SAP duplicate check (supplier, currency, gross amount, plus optional fields); Oracle unique number per supplier plus optional additional check; Dynamics Reject duplicate; NetSuite warning | Clean, and a separate control from matching in every system. Keys differ: SAP always includes amount; Oracle number first; NetSuite and Dynamics number based. |
| Rounding drift | SAP BD and total-based small differences (posted to a small differences account); Dynamics round-off as an invoice total; Oracle Dist Variance is exact (no tolerance stated) | Clean only in SAP (explicit auto-accept small difference). Others: none found. |

### Observations for the later tickets (facts, not decisions)

- SAP's tolerance key list doubles as its taxonomy. Oracle's hold names do the same. Dynamics and NetSuite name per-field checks rather than a flat list.
- Severity in all four is binary: warn or block/hold, with human release. None documents graded severities.
- SAP and Dynamics let tolerances vary by scope (company code, vendor, item). Oracle assigns them by supplier site. NetSuite by item, vendor, subsidiary.
- Two systems (SAP with lower and upper limits, Dynamics with an optional negative flag) treat over-billing and under-billing as separately configurable. Oracle treats only over-billing.
- Because vendors share no default numbers, any docmatch default must be justified by the benchmark, not by vendor practice.
