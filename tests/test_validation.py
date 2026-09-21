from types import SimpleNamespace as Obj

from app.extraction import candidates_from_result
from app.validation import review_candidates, validate_record


def test_valid_and_invalid_identity_formats():
    record = dict(
        full_name="Test Person",
        email="person@example.com",
        phone="9876543210",
        pan="ABCDE1234F",
        aadhaar="234567890123",
    )
    assert validate_record(record, "engineering") == []
    record.update(pan="wrong", aadhaar="123456789012")
    assert set(validate_record(record, "unknown")) == {"pan", "aadhaar", "department"}


def test_conflicts_and_low_confidence_require_review():
    result = review_candidates(
        [
            {"field": "pan", "value": "ABCDE1234F", "confidence": 0.99},
            {"field": "pan", "value": "ABCDE1234G", "confidence": 0.99},
            {"field": "email", "value": "x@y.com", "confidence": 0.5},
        ]
    )
    assert result["accepted"] == {}
    assert set(result["review_fields"]) == {"pan", "email"}


def test_ocr_provenance_and_missing_confidence():
    line = Obj(content="ABCDE1234F", spans=[])
    result = candidates_from_result(Obj(pages=[Obj(lines=[line], words=[], page_number=2)]), "doc-1")
    assert result["candidates"][0]["document_id"] == "doc-1"
    assert result["candidates"][0]["page"] == 2
    assert result["review_fields"] == ["pan"]
