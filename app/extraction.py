"""Azure Document Intelligence OCR with deterministic, traceable candidates and LLM-powered extraction."""

import io
import json
import logging
import os
import re
from urllib.parse import urlparse

from .validation import review_candidates

PATTERNS = {
    "pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
    "aadhaar": r"\b[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}\b",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "phone": r"(?:\+91[\-\s]?)?[6-9]\d{9}\b|(?:\+?[0-9]{1,3}[\-\s]?)?\(?[0-9]{2,5}\)?[\-\s]?[0-9]{3,4}[\-\s]?[0-9]{3,4}\b",
    "dob": r"\b(?:0?[1-9]|[12][0-9]|3[01])[\/\-\.](?:0?[1-9]|1[012])[\/\-\.](?:19|20)\d{2}\b",
}


def extract_with_llm(full_text: str, document_id: str) -> list[dict]:
    """Use deployed gpt-4.1-mini to accurately parse all fields from document text."""
    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
    key = os.getenv("AZURE_OPENAI_API_KEY", "")
    if not endpoint or not key or not full_text.strip():
        return []
    try:
        from openai import OpenAI
        base_url = endpoint.rstrip("/") + "/openai/v1/"
        client = OpenAI(api_key=key, base_url=base_url, timeout=30.0, max_retries=1, default_headers={"api-key": key})

        prompt = (
            "You are an expert HR onboarding document parser. Analyze the OCR text extracted from an employee document "
            "(Resume/CV, PAN Card, Aadhaar Card, ID proof, or other onboarding document). "
            "Extract all available information accurately into valid JSON.\n\n"
            "Return a JSON object with EXACTLY these keys:\n"
            "- full_name: The person's legal or prominent full name (string or null). On resumes, look for candidate's name at top.\n"
            "- email: Email address (string or null).\n"
            "- phone: Phone number (string or null, e.g. +91 9876543210).\n"
            "- pan: 10-character PAN number matching [A-Z]{5}[0-9]{4}[A-Z] (string or null).\n"
            "- aadhaar: 12-digit Aadhaar number, digits only (string or null).\n"
            "- address: Full residential or permanent address (string or null).\n"
            "- dob: Date of birth in DD/MM/YYYY or YYYY-MM-DD format (string or null).\n"
            "- doc_type: Best classification: 'pan', 'aadhaar', 'resume', 'photograph', or 'other'.\n\n"
            f"Document OCR text:\n{full_text[:6000]}"
        )

        resp = client.chat.completions.create(
            model=os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini"),
            messages=[
                {"role": "system", "content": "You extract structured onboarding entity data from documents into clean JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        data = json.loads(resp.choices[0].message.content or "{}")

        candidates = []
        for field in ("full_name", "email", "phone", "pan", "aadhaar", "address", "dob"):
            val = data.get(field)
            if val and isinstance(val, str) and val.strip() and val.lower() not in {"null", "none", "n/a", "unknown"}:
                val = val.strip()
                if field == "aadhaar":
                    val = re.sub(r"[ -]", "", val)
                if field == "phone":
                    val = re.sub(r"[ ()-]", "", val)
                candidates.append({
                    "field": field,
                    "value": val,
                    "confidence": 0.98,
                    "document_id": document_id,
                    "page": 1,
                    "line_offset": 0,
                    "method": "llm-structured-extraction",
                    "source": "azure-openai",
                    "doc_type_hint": data.get("doc_type")
                })
        return candidates
    except Exception as exc:
        logging.getLogger(__name__).warning("LLM extraction failed: %s", exc)
        return []


def candidates_from_result(result, document_id: str) -> dict:
    candidates = []

    # 1. Collect full text across all pages
    full_text = getattr(result, "content", "") or ""
    if not full_text:
        lines = []
        for page in getattr(result, "pages", []) or []:
            for line in getattr(page, "lines", []) or []:
                lines.append(line.content)
        full_text = "\n".join(lines)

    # 2. Extract using AI / LLM (gpt-4.1-mini)
    llm_candidates = extract_with_llm(full_text, document_id)
    candidates.extend(llm_candidates)

    # 3. Add regex pattern candidates
    for page in getattr(result, "pages", []) or []:
        for line in getattr(page, "lines", []) or []:
            words = [
                word
                for word in getattr(page, "words", []) or []
                if any(
                    span.offset <= word.span.offset < span.offset + span.length for span in getattr(line, "spans", []) or []
                )
            ]
            confidence = max(min((word.confidence for word in words), default=0.95), 0.95)
            for field, pattern in PATTERNS.items():
                for match in re.finditer(pattern, line.content):
                    value = match.group().strip()
                    if field == "phone":
                        value = re.sub(r"[ ()-]", "", value)
                    if field == "email":
                        value = value.lower()
                    if field == "aadhaar":
                        value = re.sub(r"[ -]", "", value)
                    if not any(c["field"] == field for c in candidates):
                        candidates.append(
                            {
                                "field": field,
                                "value": value,
                                "confidence": confidence,
                                "document_id": document_id,
                                "page": getattr(page, "page_number", 1),
                                "line_offset": line.spans[0].offset if line.spans else None,
                                "method": "pattern",
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

    with DocumentIntelligenceClient(endpoint, document_credential(endpoint), retry_total=3) as client:
        result = client.begin_analyze_document("prebuilt-read", body=io.BytesIO(content)).result(timeout=120)
    return candidates_from_result(result, document_id)


def classify_document(candidates: list[dict], filename: str = "", content_type: str = "") -> str:
    """Auto-classify document type from OCR extraction candidates, filename, and mime type."""
    fn_lower = (filename or "").lower()
    ct_lower = (content_type or "").lower()

    if any(k in fn_lower for k in ("photo", "pic", "profile", "headshot", "avatar", "image")):
        return "photograph"
    if ct_lower.startswith("image/") and not candidates:
        return "photograph"

    # Check for doc_type_hint from LLM
    for c in candidates:
        hint = c.get("doc_type_hint", "")
        if hint and isinstance(hint, str):
            hint_lower = hint.lower()
            if "pan" in hint_lower:
                return "pan"
            if "aadhaar" in hint_lower or "aadhar" in hint_lower:
                return "aadhaar"
            if "resume" in hint_lower or "cv" in hint_lower:
                return "resume"

    fields_found = {c["field"] for c in candidates}
    if "pan" in fields_found:
        return "pan"
    if "aadhaar" in fields_found:
        return "aadhaar"
    if "email" in fields_found or "phone" in fields_found or "resume" in fn_lower:
        return "resume"
    if ct_lower.startswith("image/"):
        return "photograph"
    return "other"
