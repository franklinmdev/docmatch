"""Tests for scoring a predicted set of header fields against the labeled set."""

import pytest

from docmatch.docile.annotation import Annotation, DocumentMetadata, FieldExtraction
from docmatch.metrics.fields import Prediction, labeled_fields, score_fields


def labels(*pairs: tuple[str, str]) -> dict[str, tuple[str, ...]]:
    """Labeled values as the scorer takes them, fieldtype to every value."""
    grouped: dict[str, tuple[str, ...]] = {}
    for fieldtype, text in pairs:
        grouped[fieldtype] = (*grouped.get(fieldtype, ()), text)
    return grouped


def test_a_prediction_that_repeats_every_label_scores_one() -> None:
    gold = labels(("vendor_name", "Synthetic Supplies Ltd"), ("amount_due", "236.00"))

    score = score_fields(gold, gold)

    assert (score.precision, score.recall, score.f1) == (1.0, 1.0, 1.0)
    assert score.false_positives == score.false_negatives == 0


def test_a_label_the_prediction_lacks_is_a_miss() -> None:
    score = score_fields(labels(("vendor_name", "Acme"), ("document_id", "A-1")), {})

    assert (score.true_positives, score.false_negatives, score.false_positives) == (
        0,
        2,
        0,
    )
    assert (score.recall, score.f1) == (0.0, 0.0)
    assert score.precision == 1.0, "nothing was predicted, so nothing was wrong"


def test_a_prediction_the_label_lacks_is_a_false_positive() -> None:
    score = score_fields({}, labels(("vendor_name", "Acme")))

    assert (score.true_positives, score.false_negatives, score.false_positives) == (
        0,
        0,
        1,
    )
    assert (score.precision, score.f1) == (0.0, 0.0)


def test_a_value_only_formatted_differently_still_matches() -> None:
    score = score_fields(
        labels(("amount_due", "$2,460.00"), ("date_issue", "SEP18/26")),
        labels(("amount_due", "2460"), ("date_issue", "2026-09-18")),
    )

    assert score.true_positives == 2
    assert score.f1 == 1.0


def test_scoring_is_reported_per_fieldtype() -> None:
    score = score_fields(
        labels(("vendor_name", "Acme"), ("document_id", "A-1")),
        labels(("vendor_name", "Acme"), ("document_id", "B-2")),
    )

    by_fieldtype = {each.fieldtype: each for each in score.per_fieldtype}
    assert by_fieldtype["vendor_name"].matched == ("acme",)
    assert by_fieldtype["document_id"].matched == ()
    assert by_fieldtype["document_id"].missing == ("a-1",)
    assert by_fieldtype["document_id"].spurious == ("b-2",)


def test_one_value_labeled_twice_is_one_value() -> None:
    """DocILE localizes every occurrence on the page; an extractor reports values."""
    score = score_fields(
        labels(("vendor_name", "Acme"), ("vendor_name", "ACME")),
        labels(("vendor_name", "Acme")),
    )

    assert (score.true_positives, score.false_negatives) == (1, 0)
    assert score.f1 == 1.0


def test_two_different_values_of_one_fieldtype_both_have_to_be_predicted() -> None:
    score = score_fields(
        labels(("tax_detail_rate", "8.25%"), ("tax_detail_rate", "5%")),
        labels(("tax_detail_rate", "8.25")),
    )

    assert (score.true_positives, score.false_negatives) == (1, 1)
    assert score.recall == 0.5


def test_a_document_with_no_labels_and_no_prediction_scores_one() -> None:
    """Vacuous, but it must not be a zero that drags an aggregate down."""
    assert score_fields({}, {}).f1 == 1.0


def test_labeled_fields_groups_an_annotation_by_fieldtype() -> None:
    annotation = Annotation(
        fields=[
            FieldExtraction(
                fieldtype="vendor_name", text="Acme", page=0, bbox=(0, 0, 1, 1)
            ),
            FieldExtraction(
                fieldtype="vendor_name", text="Acme", page=1, bbox=(0, 0, 1, 1)
            ),
            FieldExtraction(
                fieldtype="amount_due", text="10.00", page=0, bbox=(0, 0, 1, 1)
            ),
        ],
        cells=[],
        metadata=DocumentMetadata(page_count=2),
    )

    assert labeled_fields(annotation) == {
        "vendor_name": ("Acme", "Acme"),
        "amount_due": ("10.00",),
    }


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"vendor_name": "Acme"}', {"vendor_name": ("Acme",)}),
        ('{"tax_detail_rate": ["8.25%", "5%"]}', {"tax_detail_rate": ("8.25%", "5%")}),
        ('{"date_due": null}', {"date_due": ()}),
        ('{"date_due": ""}', {"date_due": ()}),
    ],
)
def test_a_prediction_file_takes_one_value_a_list_or_an_absence(
    body: str, expected: dict[str, tuple[str, ...]]
) -> None:
    assert Prediction.model_validate_json(body).fields == expected


def test_an_absent_value_is_neither_predicted_nor_wrong() -> None:
    """Absence is written down, not inferred from a missing key."""
    prediction = Prediction.model_validate_json('{"date_due": null}')

    score = score_fields({}, prediction.fields)

    assert score.false_positives == 0


def test_a_value_that_normalizes_to_nothing_is_not_a_value() -> None:
    """`score_fields` is the seam an extractor calls, not only the file reader."""
    score = score_fields(
        labels(("vendor_name", "Acme")), labels(("vendor_name", "   "))
    )

    assert (score.true_positives, score.false_positives, score.false_negatives) == (
        0,
        0,
        1,
    )
