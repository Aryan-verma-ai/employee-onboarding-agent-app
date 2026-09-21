"""Deterministic onboarding rules. OCR candidates never establish identity."""

import re

DEPARTMENTS = {"engineering", "hr", "finance", "sales", "operations"}
REQUIRED_FIELDS = ("full_name", "email", "phone", "pan", "aadhaar")
REQUIRED_DOCUMENTS = ("pan", "aadhaar")


def validate_record(data: dict, department: str) -> list[str]:
    errors = []
    if department.lower() not in DEPARTMENTS:
        errors.append("department")
    for field in REQUIRED_FIELDS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(field)
    checks = {
        "email": r"[^\s@]+@[^\s@]+\.[^\s@]+",
        "phone": r"\+?[0-9][0-9 -]{8,17}",
        "pan": r"[A-Z]{5}[0-9]{4}[A-Z]",
        "aadhaar": r"[2-9][0-9]{11}",
    }
    for field, pattern in checks.items():
        value = data.get(field)
        if isinstance(value, str) and value and not re.fullmatch(pattern, value):
            errors.append(field)
    return list(dict.fromkeys(errors))


def review_candidates(candidates: list[dict], threshold: float = 0.90) -> dict:
    """Keep conflicting/uncertain evidence out of the accepted record."""
    grouped = {}
    for candidate in candidates:
        grouped.setdefault(candidate["field"], []).append(candidate)
    accepted, review = {}, []
    for field, evidence in grouped.items():
        values = {item["value"] for item in evidence}
        if len(values) != 1 or min(item["confidence"] for item in evidence) < threshold:
            review.append(field)
        else:
            accepted[field] = evidence[0]["value"]
    return {"accepted": accepted, "review_fields": review, "candidates": candidates}
