"""Azure Document Intelligence OCR with deterministic, traceable candidates."""

import io
import os
import re
from urllib.parse import urlparse

from .validation import review_candidates

PATTERNS = {
    "pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
    "aadhaar": r"\b[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}\b",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "phone": r"(?i)(?<=phone:)[ +0-9()-]{9,24}|(?<=mobile:)[ +0-9()-]{9,24}",
    "full_name": r"(?i)(?<=name:)[ A-Za-z.'-]{2,100}",
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
                    value = value.strip()
                    if field == "phone":
                        value = re.sub(r"[ ()-]", "", value)
                    if field == "full_name":
                        value = " ".join(value.split())
                    if field == "email":
                        value = value.lower()
                    if field == "aadhaar":
                        value = re.sub(r"[ -]", "", value)
                    candidates.append(
                        {
                            "field": field,
                            "value": value,
                            "confidence": confidence,
                            "document_id": document_id,
                            "page": page.page_number,
                            "line_offset": line.spans[0].offset if line.spans else None,
                            "method": "labeled-pattern" if field in {"full_name", "phone"} else "pattern",
                            "source": "azure-document-intelligence",
                        }
                    )
    return review_candidates(candidates)


def document_credential(endpoint):
    mode = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE", "identity")
    if mode == "identity":
        from app.azure_auth import azure_credential

        return azure_credential()
    if mode != "api_key":
        raise ValueError("Unsupported Document Intelligence authentication mode")
    from azure.core.credentials import AzureKeyCredential

    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_API_KEY")
    if not key:
        target = urlparse(endpoint)
        foundry = urlparse(os.getenv("FOUNDRY_PROJECT_ENDPOINT", ""))
        if target.scheme != "https" or target.hostname != foundry.hostname or not foundry.hostname:
            raise ValueError("Shared API key requires the same HTTPS Foundry resource host")
        key = os.getenv("AZURE_OPENAI_API_KEY")
    if not key:
        raise ValueError("Document Intelligence API key is not configured")
    return AzureKeyCredential(key)


def extract_document(content: bytes, document_id: str) -> dict:
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("Document Intelligence endpoint is not configured")
    from azure.ai.documentintelligence import DocumentIntelligenceClient

    # SDK retries transient HTTP failures; failed work remains explicitly retryable.
    with DocumentIntelligenceClient(endpoint, document_credential(endpoint), retry_total=3) as client:
        result = client.begin_analyze_document("prebuilt-read", body=io.BytesIO(content)).result(timeout=120)
    return candidates_from_result(result, document_id)
