"""Survey of which candidate gate rules DocILE's own labels pass.

Throwaway script for GitHub issue #19. Reads the local DocILE dataset
(never committed) and reuses docmatch's own loading and normalization code.
Prints counts, shares and fieldtype names only, no label text.
"""

import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATA_ROOT = Path("/home/franklinmdev/docmatch/data/docile")
"""The dataset is never committed, so this stays an absolute, local path."""

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine" / "src"))

from docmatch.docile.annotation import Annotation, FieldExtraction  # noqa: E402
from docmatch.docile.dataset import DocileDataset  # noqa: E402
from docmatch.metrics.fields import by_fieldtype  # noqa: E402
from docmatch.metrics.normalization import normalize, normalize_number  # noqa: E402
from docmatch.metrics.score import ratio  # noqa: E402

SPLIT = "trainval"


def source_of(document_id: str) -> str | None:
    """Read metadata.source directly; the Annotation model does not carry it."""
    import json

    path = DATA_ROOT / "annotations" / f"{document_id}.json"
    return json.loads(path.read_bytes())["metadata"].get("source")


def header_values(fields: list[FieldExtraction], fieldtype: str) -> tuple[str, ...]:
    """Distinct normalized values of one header fieldtype, in document order."""
    texts = by_fieldtype(fields).get(fieldtype, ())
    seen: list[str] = []
    for text in texts:
        value = normalize(fieldtype, text)
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def as_decimal(fieldtype: str, text: str) -> Decimal | None:
    value = normalize_number(text)
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def one_amount(
    fields: list[FieldExtraction], fieldtype: str
) -> tuple[Decimal | None, bool]:
    """The single decimal value of a header amount fieldtype, and whether ambiguous."""
    values = header_values(fields, fieldtype)
    if len(values) == 0:
        return None, False
    if len(values) > 1:
        return None, True
    try:
        return Decimal(values[0]), False
    except InvalidOperation:
        return None, False


def close(
    a: Decimal, b: Decimal, *, cents: Decimal | None, pct: Decimal | None
) -> bool:
    diff = abs(a - b)
    if cents is not None and diff <= cents:
        return True
    if pct is not None and b != 0 and diff / abs(b) <= pct:
        return True
    if cents is None and pct is None:
        return diff == 0
    return False


TOLERANCES = {
    "exact": (Decimal("0"), None),
    "1 cent": (Decimal("0.01"), None),
    "0.5%": (None, Decimal("0.005")),
}


def main() -> None:
    dataset = DocileDataset(DATA_ROOT)
    ids = dataset.document_ids(SPLIT)
    docs: list[tuple[str, Annotation, str | None]] = []
    for doc_id in ids:
        ann = dataset.annotation(doc_id)
        docs.append((doc_id, ann, source_of(doc_id)))

    all_docs = docs
    ucsf_docs = [d for d in docs if d[2] == "ucsf"]

    print(f"Corpus  trainval, all={len(all_docs)}, ucsf={len(ucsf_docs)}")
    print()

    survey_line_totals(all_docs, ucsf_docs)
    survey_tax_arithmetic(all_docs, ucsf_docs)
    survey_totals_agree(all_docs, ucsf_docs)
    survey_dates_sane(all_docs, ucsf_docs)
    survey_identifiers(all_docs, ucsf_docs)
    survey_line_total_failure_modes(all_docs, ucsf_docs)
    survey_totals_agree_failure_modes(all_docs, ucsf_docs)


def survey_line_totals(all_docs, ucsf_docs) -> None:
    print("== Rule: line amounts reconcile to the subtotal ==")
    for label, group in (("all", all_docs), ("ucsf", ucsf_docs)):
        for net_field, total_field in (
            ("line_item_amount_net", "amount_total_net"),
            ("line_item_amount_gross", "amount_total_gross"),
        ):
            carries = 0
            ambiguous_total = 0
            results = Counter()
            for _, ann, _ in group:
                rows = [row for row in ann.line_items]
                row_values = []
                any_cell = False
                unreadable_cell = False
                for row in rows:
                    cells = by_fieldtype(row.cells)
                    texts = cells.get(net_field, ())
                    if texts:
                        any_cell = True
                        dec = as_decimal(net_field, texts[0])
                        if dec is None:
                            unreadable_cell = True
                        else:
                            row_values.append(dec)
                total, ambiguous = one_amount(ann.fields, total_field)
                if not any_cell or total is None and not ambiguous:
                    continue
                carries += 1
                if ambiguous:
                    ambiguous_total += 1
                    results["ambiguous total"] += 1
                    continue
                if unreadable_cell:
                    results["unreadable cell"] += 1
                    continue
                line_sum = sum(row_values, Decimal("0"))
                passed = {
                    tol: close(line_sum, total, cents=c, pct=p)
                    for tol, (c, p) in TOLERANCES.items()
                }
                if passed["exact"]:
                    results["pass exact"] += 1
                elif passed["1 cent"]:
                    results["pass within 1 cent"] += 1
                elif passed["0.5%"]:
                    results["pass within 0.5%"] += 1
                else:
                    results["fail beyond tolerance"] += 1
            print(
                f"  [{label}] {net_field} -> {total_field}: "
                f"carry={carries} ({ratio(carries, len(group)):.1%} of docs)"
            )
            for key in (
                "pass exact",
                "pass within 1 cent",
                "pass within 0.5%",
                "fail beyond tolerance",
                "ambiguous total",
                "unreadable cell",
            ):
                if results[key]:
                    share = ratio(results[key], carries)
                    print(f"      {key}: {results[key]} ({share:.1%} of carriers)")
    print()


def survey_tax_arithmetic(all_docs, ucsf_docs) -> None:
    print("== Rule: tax arithmetic is consistent (net + tax = gross) ==")
    for label, group in (("all", all_docs), ("ucsf", ucsf_docs)):
        carries = 0
        results = Counter()
        for _, ann, _ in group:
            net, net_amb = one_amount(ann.fields, "amount_total_net")
            tax, tax_amb = one_amount(ann.fields, "amount_total_tax")
            gross, gross_amb = one_amount(ann.fields, "amount_total_gross")
            have_net = net is not None or net_amb
            have_tax = tax is not None or tax_amb
            have_gross = gross is not None or gross_amb
            if not (have_net and have_tax and have_gross):
                continue
            carries += 1
            if net_amb or tax_amb or gross_amb:
                results["ambiguous field"] += 1
                continue
            expected = net + tax
            passed = {
                tol: close(expected, gross, cents=c, pct=p)
                for tol, (c, p) in TOLERANCES.items()
            }
            if passed["exact"]:
                results["pass exact"] += 1
            elif passed["1 cent"]:
                results["pass within 1 cent"] += 1
            elif passed["0.5%"]:
                results["pass within 0.5%"] += 1
            else:
                results["fail beyond tolerance"] += 1
                # sub-categorize: multiple tax rates present?
                rates = header_values(ann.fields, "tax_detail_rate")
                if len(rates) > 1:
                    results["fail, multiple tax_detail_rate values"] += 1
        share = ratio(carries, len(group))
        print(f"  [{label}] carry all three amount_total_*: {carries} ({share:.1%})")
        for key, count in sorted(results.items(), key=lambda kv: -kv[1]):
            print(f"      {key}: {count} ({ratio(count, carries):.1%} of carriers)")
    print()


def survey_totals_agree(all_docs, ucsf_docs) -> None:
    print("== Rule: totals agree (gross - paid = due, or due = gross if unpaid) ==")
    for label, group in (("all", all_docs), ("ucsf", ucsf_docs)):
        carries_with_paid = 0
        carries_without_paid = 0
        results_with = Counter()
        results_without = Counter()
        for _, ann, _ in group:
            gross, gross_amb = one_amount(ann.fields, "amount_total_gross")
            due, due_amb = one_amount(ann.fields, "amount_due")
            paid, paid_amb = one_amount(ann.fields, "amount_paid")
            has_paid_field = len(header_values(ann.fields, "amount_paid")) > 0
            have_gross = gross is not None or gross_amb
            have_due = due is not None or due_amb
            if not (have_gross and have_due):
                continue
            if has_paid_field:
                carries_with_paid += 1
                if gross_amb or due_amb or paid_amb or paid is None:
                    results_with["ambiguous or unreadable"] += 1
                    continue
                expected = gross - paid
                passed = {
                    tol: close(expected, due, cents=c, pct=p)
                    for tol, (c, p) in TOLERANCES.items()
                }
                if passed["exact"]:
                    results_with["pass exact"] += 1
                elif passed["1 cent"]:
                    results_with["pass within 1 cent"] += 1
                elif passed["0.5%"]:
                    results_with["pass within 0.5%"] += 1
                else:
                    results_with["fail beyond tolerance"] += 1
            else:
                carries_without_paid += 1
                if gross_amb or due_amb:
                    results_without["ambiguous"] += 1
                    continue
                passed = {
                    tol: close(gross, due, cents=c, pct=p)
                    for tol, (c, p) in TOLERANCES.items()
                }
                if passed["exact"]:
                    results_without["pass exact"] += 1
                elif passed["1 cent"]:
                    results_without["pass within 1 cent"] += 1
                elif passed["0.5%"]:
                    results_without["pass within 0.5%"] += 1
                else:
                    results_without["fail beyond tolerance"] += 1
        print(
            f"  [{label}] carry gross+due+paid = {carries_with_paid} "
            f"({ratio(carries_with_paid, len(group)):.1%} of docs)"
        )
        for key, count in sorted(results_with.items(), key=lambda kv: -kv[1]):
            share = ratio(count, carries_with_paid)
            print(f"      {key}: {count} ({share:.1%} of carriers)")
        print(
            f"  [{label}] carry gross+due, no amount_paid = {carries_without_paid} "
            f"({ratio(carries_without_paid, len(group)):.1%} of docs)"
        )
        for key, count in sorted(results_without.items(), key=lambda kv: -kv[1]):
            share = ratio(count, carries_without_paid)
            print(f"      {key}: {count} ({share:.1%} of carriers)")
    print()


def survey_dates_sane(all_docs, ucsf_docs) -> None:
    print("== Rule: dates are sane (date_issue <= date_due) ==")
    for label, group in (("all", all_docs), ("ucsf", ucsf_docs)):
        carries = 0
        results = Counter()
        for _, ann, _ in group:
            issue_values = header_values(ann.fields, "date_issue")
            due_values = header_values(ann.fields, "date_due")
            if not issue_values or not due_values:
                continue
            carries += 1
            if len(issue_values) > 1 or len(due_values) > 1:
                results["ambiguous (multiple distinct dates)"] += 1
                continue
            issue, due = issue_values[0], due_values[0]
            if "-" not in issue or "-" not in due:
                results["unreadable date, fell back to text"] += 1
                continue
            if issue <= due:
                results["pass, issue <= due"] += 1
            else:
                results["fail, issue > due"] += 1
        share = ratio(carries, len(group))
        print(f"  [{label}] carry date_issue and date_due: {carries} ({share:.1%})")
        for key, count in sorted(results.items(), key=lambda kv: -kv[1]):
            print(f"      {key}: {count} ({ratio(count, carries):.1%} of carriers)")
    print()


def iban_valid(text: str) -> bool | None:
    """ISO 7064 mod-97-10 check, per ISO 13616. None if not IBAN-shaped."""
    import re

    cleaned = re.sub(r"\s+", "", text).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", cleaned):
        return None
    rearranged = cleaned[4:] + cleaned[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def bic_format_valid(text: str) -> bool | None:
    import re

    cleaned = re.sub(r"\s+", "", text).upper()
    if not cleaned:
        return None
    return re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", cleaned) is not None


def survey_identifiers(all_docs, ucsf_docs) -> None:
    print("== Identifier checksums ==")
    identifier_fieldtypes = [
        "iban",
        "bic",
        "vendor_tax_id",
        "customer_tax_id",
        "vendor_registration_id",
        "customer_registration_id",
        "account_num",
        "bank_num",
    ]
    for label, group in (("all", all_docs), ("ucsf", ucsf_docs)):
        for fieldtype in identifier_fieldtypes:
            doc_count = 0
            label_count = 0
            checkable = 0
            passed = 0
            for _, ann, _ in group:
                values = by_fieldtype(ann.fields).get(fieldtype, ())
                if not values:
                    continue
                doc_count += 1
                for text in values:
                    label_count += 1
                    if fieldtype == "iban":
                        result = iban_valid(text)
                    elif fieldtype == "bic":
                        result = bic_format_valid(text)
                    else:
                        result = None
                    if result is not None:
                        checkable += 1
                        passed += result
            if doc_count == 0:
                continue
            line = (
                f"  [{label}] {fieldtype}: docs={doc_count} "
                f"({ratio(doc_count, len(group)):.1%} of docs), labels={label_count}"
            )
            if checkable:
                share = ratio(passed, checkable)
                line += f", checkable={checkable}, pass={passed} ({share:.1%})"
            else:
                line += ", no checksum/format rule implemented"
            print(line)
    print()


def survey_line_total_failure_modes(all_docs, ucsf_docs) -> None:
    print("== Failure modes: line_item_amount_gross -> amount_total_gross ==")
    for label, group in (("all", all_docs), ("ucsf", ucsf_docs)):
        tally = Counter()
        n_failed = 0
        under = over = 0
        buckets = Counter()
        for _, ann, _ in group:
            rows = ann.line_items
            row_values = []
            any_cell = False
            unreadable = False
            for row in rows:
                cells = by_fieldtype(row.cells)
                texts = cells.get("line_item_amount_gross", ())
                if texts:
                    any_cell = True
                    dec = as_decimal("line_item_amount_gross", texts[0])
                    if dec is None:
                        unreadable = True
                    else:
                        row_values.append(dec)
            total, ambiguous = one_amount(ann.fields, "amount_total_gross")
            if not any_cell or total is None or unreadable or ambiguous:
                continue
            line_sum = sum(row_values, Decimal("0"))
            if total != 0 and abs(line_sum - total) / abs(total) <= Decimal("0.005"):
                continue
            if total == 0 and line_sum == total:
                continue
            n_failed += 1
            for tag in categorize_line_total_failure(ann):
                tally[tag] += 1
            if total != 0:
                rel_diff = (line_sum - total) / abs(total)
                if rel_diff < 0:
                    under += 1
                else:
                    over += 1
                magnitude = abs(rel_diff)
                if magnitude <= Decimal("0.05"):
                    buckets["within 5%"] += 1
                elif magnitude <= Decimal("0.20"):
                    buckets["5% to 20%"] += 1
                else:
                    buckets["over 20%"] += 1
        print(f"  [{label}] failing beyond 0.5% tolerance: {n_failed}")
        for tag, count in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"      {tag}: {count} ({ratio(count, n_failed):.1%} of failures)")
        print(f"  [{label}] direction: under={under} over={over}")
        for tag, count in sorted(buckets.items(), key=lambda kv: -kv[1]):
            print(f"      {tag}: {count}")
    print()


def survey_totals_agree_failure_modes(all_docs, ucsf_docs) -> None:
    print("== Failure modes: totals agree, no amount_paid (gross == due) ==")
    for label, group in (("all", all_docs), ("ucsf", ucsf_docs)):
        tally = Counter()
        n_failed = 0
        for _, ann, _ in group:
            gross, gross_amb = one_amount(ann.fields, "amount_total_gross")
            due, due_amb = one_amount(ann.fields, "amount_due")
            has_paid_field = len(header_values(ann.fields, "amount_paid")) > 0
            if gross is None or due is None or has_paid_field or gross_amb or due_amb:
                continue
            if gross == due:
                continue
            if gross != 0 and abs(gross - due) / abs(gross) <= Decimal("0.005"):
                continue
            n_failed += 1
            for tag in categorize_totals_agree_failure(ann):
                tally[tag] += 1
        print(f"  [{label}] failing beyond 0.5% tolerance: {n_failed}")
        for tag, count in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"      {tag}: {count} ({ratio(count, n_failed):.1%} of failures)")
    print()


def categorize_line_total_failure(ann) -> set[str]:
    tags = set()
    rows = ann.line_items
    net_texts_per_row = []
    for row in rows:
        cells = by_fieldtype(row.cells)
        net_texts_per_row.append(cells.get("line_item_amount_gross", ()))
    if any(len(t) == 0 for t in net_texts_per_row) and any(
        len(t) > 0 for t in net_texts_per_row
    ):
        tags.add("some row lacks line_item_amount_gross")
    for row in rows:
        cells = by_fieldtype(row.cells)
        if cells.get("line_item_discount_amount") or cells.get(
            "line_item_discount_rate"
        ):
            tags.add("line-level discount present")
            break
    currencies = set()
    for row in rows:
        cells = by_fieldtype(row.cells)
        for text in cells.get("line_item_currency", ()):
            currencies.add(normalize("line_item_currency", text))
    header_currency = header_values(ann.fields, "currency_code_amount_due")
    if len(currencies) > 1:
        tags.add("multiple line_item_currency values")
    if header_currency and currencies and set(header_currency) != currencies:
        tags.add("line currency differs from currency_code_amount_due")
    tax_detail_rates = header_values(ann.fields, "tax_detail_rate")
    if len(tax_detail_rates) > 1:
        tags.add("multiple tax_detail_rate values")
    if not tags:
        tags.add("uncategorized (amounts present, sum genuinely differs)")
    return tags


def categorize_totals_agree_failure(ann) -> set[str]:
    tags = set()
    paid_values = header_values(ann.fields, "amount_paid")
    if paid_values:
        tags.add("amount_paid present but not matching gross-due gap")
    currency_values = header_values(ann.fields, "currency_code_amount_due")
    if len(currency_values) > 1:
        tags.add("multiple currency_code_amount_due values")
    tax_values = header_values(ann.fields, "amount_total_tax")
    if tax_values:
        tags.add("amount_total_tax also present")
    if not tags:
        tags.add("uncategorized (gross and due present, genuinely differ)")
    return tags


if __name__ == "__main__":
    main()
