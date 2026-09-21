"""Azure Document Intelligence OCR with deterministic, traceable candidates."""

import io
import os
import re

from .validation import review_candidates

PATTERNS = {
    "pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
    "aadhaar": r"\b[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}\b",
    "email": r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b",
}


def candidates_from_result(result, document_id: str) -> dict:
    candidates = []
    for page in result.pages or []:
        for line in page.lines or []:
            words = [
                word
                for word in page.words or []
                if any(
                    span.offset <= word.span.offset < span.offset + span.length for span in line.spans or []
                )
            ]
            # No confidence evidence is deliberately treated as requiring review.
            confidence = min((word.confidence for word in words), default=0.0)
            for field, pattern in PATTERNS.items():
                for match in re.finditer(pattern, line.content):
                    value = match.group()
                    if field == "aadhaar":
                        value = re.sub(r"[ -]", "", value)
                    candidates.append(
                        {
                            "field": field,
                            "value": value,
                            "confidence": confidence,
                            "document_id": document_id,
                            "page": page.page_number,
                            "source": "azure-document-intelligence",
                        }
                    )
    return review_candidates(candidates)


def extract_document(content: bytes, document_id: str) -> dict:
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("Document Intelligence endpoint is not configured")
    from azure.ai.documentintelligence import DocumentIntelligenceClient

    from app.azure_auth import azure_credential

    # SDK retries transient HTTP failures; failed work remains explicitly retryable.
    with DocumentIntelligenceClient(endpoint, azure_credential(), retry_total=3) as client:
        result = client.begin_analyze_document("prebuilt-read", body=io.BytesIO(content)).result(timeout=120)
    return candidates_from_result(result, document_id)
