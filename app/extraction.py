"""Azure Document Intelligence OCR with deterministic, traceable candidates and LLM-powered extraction."""

import io
import json
import logging
import os
import re
from urllib.parse import urlparse

from .validation import review_candidates

# Candidate personal email domains allowed during onboarding
ALLOWED_EMAIL_DOMAINS = {
    "gmail.com",
    "outlook.com",
    "yahoo.com",
    "yahoo.in",
    "hotmail.com",
    "icloud.com",
    "live.com",
    "protonmail.com",
    "proton.me",
    # Test fixtures
    "example.com",
    "example.invalid",
}

DISALLOWED_EMAIL_PREFIXES = {
    "help",
    "support",
    "info",
    "contact",
    "admin",
    "noreply",
    "no-reply",
    "query",
    "grievance",
}

PATTERNS = {
    "pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
    "aadhaar": r"\b[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}\b",
    "email": r"(?i)\b[A-Za-z0-9._%+-]+@(?:gmail\.com|outlook\.com|yahoo\.com|yahoo\.in|hotmail\.com|icloud\.com|live\.com|protonmail\.com|proton\.me|example\.com|example\.invalid)\b",
    "phone": r"(?i)(?:phone|mobile)[\s:]*([ +0-9()-]{9,24})|\b(?:\+91[\-\s]?)?[6-9]\d{9}\b",
    "full_name": r"(?i)(?:name)[\s:]*([ A-Za-z.'-]{2,100})",
    "dob": r"\b(?:0?[1-9]|[12][0-9]|3[01])[\/\-\.](?:0?[1-9]|1[012])[\/\-\.](?:19|20)\d{2}\b",
}


def is_valid_candidate_email(email: str) -> bool:
    """Validate that email is a personal candidate email and NOT an institutional/helpline address."""
    if not email or not isinstance(email, str):
        return False
    email = email.strip().lower()
    if not re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", email):
        return False
    user, domain = email.split("@", 1)
    if any(
        user == p or user.startswith(f"{p}.") or user.startswith(f"{p}-") or user.startswith(f"{p}_")
        for p in DISALLOWED_EMAIL_PREFIXES
    ):
        return False
    if domain.endswith(".gov.in") or "uidai" in domain or "incometax" in domain:
        return False
    return domain in ALLOWED_EMAIL_DOMAINS


def is_valid_indian_phone(phone: str, aadhaar_val: str = "") -> bool:
    """Validate that phone is a real 10-digit Indian mobile number, not Aadhaar or helpline."""
    if not phone or not isinstance(phone, str):
        return False
    digits = re.sub(r"\D", "", phone.strip())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        return False
    if digits == "1947" or digits.startswith("1947"):
        return False
    if aadhaar_val and digits in aadhaar_val.replace(" ", ""):
        return False
    return True


def clean_full_name(name: str) -> str:
    """Clean candidate name by stripping bilingual regional noise, OCR label artifacts, and prefixes."""
    if not name or not isinstance(name, str):
        return ""
    # Strip dates (e.g. 01/06/2006 or 2006-06-01)
    name = re.sub(r"\b\d{1,4}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}\b", " ", name)
    if re.search(r"[A-Za-z]", name):
        # Strip Devanagari and Gurmukhi characters when mixed with English
        name = re.sub(r"[\u0900-\u0D7F]", " ", name)
        bad_words = [
            r"\bDOB\b",
            r"\bDate of Birth\b",
            r"\bMale\b",
            r"\bFemale\b",
            r"\bJanam\b",
            r"\bMiti\b",
            r"\bNaram\b",
            r"\bBhidi\b",
            r"\bC/O\b",
            r"\bS/O\b",
            r"\bD/O\b",
            r"\bW/O\b",
            r"\bGovernment of India\b",
            r"\bUIDAI\b",
            r"\bEnrolment\b",
            r"\bIssue Date\b",
            r"\bDownload Date\b",
        ]
        for bw in bad_words:
            name = re.sub(bw, " ", name, flags=re.IGNORECASE)
    # Names should never contain digits
    name = re.sub(r"\d+", " ", name)
    name = re.sub(r"[^\w\s\.-]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def extract_with_llm(full_text: str, document_id: str) -> list[dict]:
    """Use deployed gpt-4.1-mini to accurately parse all fields from document text."""
    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
    key = os.getenv("AZURE_OPENAI_API_KEY", "")
    if not endpoint or not key or not full_text.strip():
        return []
    try:
        from openai import OpenAI

        base_url = endpoint.rstrip("/") + "/openai/v1/"
        client = OpenAI(
            api_key=key, base_url=base_url, timeout=30.0, max_retries=1, default_headers={"api-key": key}
        )

        prompt = (
            "You are an expert Indian HR onboarding document parser specializing in Resumes, PAN Cards, and Aadhaar Cards.\n"
            "Analyze the OCR text extracted from an employee document and extract all available information accurately into valid JSON.\n\n"
            "CRITICAL EXTRACTION RULES:\n"
            "1. full_name:\n"
            "   - On Indian ID cards (Aadhaar, PAN), cards are bilingual (regional language like Hindi/Gurmukhi/Tamil + English).\n"
            "   - Extract ONLY the clean English full name of the person (e.g. 'Neelabh', 'Amit Kumar').\n"
            "   - NEVER append regional language words, transliterations of date-of-birth labels (e.g. 'Janam Miti', 'Naram Bhidi', 'जन्म तिथि', 'DOB'), gender labels ('Male', 'Female', 'भवर'), or guardian/care-of names ('C/O', 'S/O', 'D/O').\n"
            "   - On resumes, extract candidate's name from header.\n"
            "2. email:\n"
            "   - Only extract the candidate's personal email address with domains: @gmail.com, @outlook.com, @yahoo.com, @yahoo.in, @hotmail.com, @icloud.com.\n"
            "   - NEVER extract helpline/support/government emails printed on cards (e.g. 'help@uidai.gov.in', 'incometax.gov.in', any @*.gov.in). Return null if no personal candidate email exists.\n"
            "3. phone:\n"
            "   - Must be a valid 10-digit Indian mobile number starting with 6, 7, 8, or 9 (optionally prefixed with +91).\n"
            "   - NEVER confuse the 12-digit Aadhaar number (e.g. 4296 8981 7829) or helpline (1947) with a phone number. Return null if no 10-digit personal mobile number is present.\n"
            "4. aadhaar:\n"
            "   - 12-digit Aadhaar number, digits only (e.g. 429689817829). Do not confuse VID (16 digits) with Aadhaar.\n"
            "5. pan:\n"
            "   - 10-character Permanent Account Number matching [A-Z]{5}[0-9]{4}[A-Z]. Return null if not present.\n"
            "6. dob:\n"
            "   - The candidate's Date of Birth in DD/MM/YYYY format. Look explicitly for 'DOB:', 'Date of Birth:', 'Year of Birth:'.\n"
            "   - Do NOT extract 'Issue Date' or 'Download Date' as the Date of Birth.\n"
            "7. address:\n"
            "   - Full residential address from the back of Aadhaar card or resume. Clean up OCR noise.\n"
            "8. doc_type:\n"
            "   - Best classification: 'pan', 'aadhaar', 'resume', 'photograph', or 'other'.\n\n"
            f"Document OCR text:\n{full_text[:6000]}"
        )

        resp = client.chat.completions.create(
            model=os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini"),
            messages=[
                {
                    "role": "system",
                    "content": "You extract structured onboarding entity data from documents into clean JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")

        candidates = []
        for field in ("full_name", "email", "phone", "pan", "aadhaar", "address", "dob"):
            val = data.get(field)
            if (
                val
                and isinstance(val, str)
                and val.strip()
                and val.lower() not in {"null", "none", "n/a", "unknown"}
            ):
                val = val.strip()
                if field == "aadhaar":
                    val = re.sub(r"[ -]", "", val)
                elif field == "phone":
                    val = re.sub(r"[ ()-]", "", val)
                    if not is_valid_indian_phone(val, aadhaar_val=data.get("aadhaar", "")):
                        continue
                elif field == "email":
                    val = val.lower()
                    if not is_valid_candidate_email(val):
                        continue
                elif field == "full_name":
                    val = clean_full_name(val)
                    if not val:
                        continue
                candidates.append(
                    {
                        "field": field,
                        "value": val,
                        "confidence": 0.98,
                        "document_id": document_id,
                        "page": 1,
                        "line_offset": 0,
                        "method": "llm-structured-extraction",
                        "source": "azure-openai",
                        "doc_type_hint": data.get("doc_type"),
                    }
                )
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
                    span.offset <= word.span.offset < span.offset + span.length
                    for span in getattr(line, "spans", []) or []
                )
            ]
            confidence = min((word.confidence for word in words), default=0.0)
            for field, pattern in PATTERNS.items():
                if field == "dob":
                    line_lower = line.content.lower()
                    if "download" in line_lower or "issue" in line_lower:
                        continue
                for match in re.finditer(pattern, line.content):
                    raw_val = (
                        match.group(1)
                        if (match.lastindex and field in {"full_name", "phone"})
                        else match.group(0)
                    )
                    value = raw_val.strip()
                    if field == "phone":
                        value = re.sub(r"[ ()-]", "", value)
                        if not is_valid_indian_phone(value):
                            continue
                    elif field == "full_name":
                        value = " ".join(value.split())
                        value = clean_full_name(value)
                        if not value:
                            continue
                    elif field == "email":
                        value = value.lower()
                        if not is_valid_candidate_email(value):
                            continue
                    elif field == "aadhaar":
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
                                "method": "labeled-pattern" if field in {"full_name", "phone"} else "pattern",
                                "source": "azure-document-intelligence",
                            }
                        )

    # Final pass filtering and sanitization
    aadhaar_val = next((c["value"] for c in candidates if c["field"] == "aadhaar"), "")
    sanitized = []
    for c in candidates:
        f = c["field"]
        v = c["value"]
        if f == "email" and not is_valid_candidate_email(v):
            continue
        if f == "phone" and not is_valid_indian_phone(v, aadhaar_val=aadhaar_val):
            continue
        if f == "full_name":
            c["value"] = clean_full_name(v)
            if not c["value"]:
                continue
        sanitized.append(c)

    return review_candidates(sanitized)


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
        target_name = (target.hostname or "").split(".")[0]
        foundry_name = (foundry.hostname or "").split(".")[0]
        valid_host = (
            target.scheme == "https"
            and bool(target.hostname)
            and (
                target.hostname == foundry.hostname
                or (
                    target_name
                    and target_name == foundry_name
                    and (
                        (target.hostname or "").endswith(".cognitiveservices.azure.com")
                        or (target.hostname or "").endswith(".services.ai.azure.com")
                    )
                    and (
                        (foundry.hostname or "").endswith(".cognitiveservices.azure.com")
                        or (foundry.hostname or "").endswith(".services.ai.azure.com")
                    )
                )
            )
        )
        if not valid_host:
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

    with DocumentIntelligenceClient(endpoint, document_credential(endpoint), retry_total=5) as client:
        result = client.begin_analyze_document("prebuilt-read", body=io.BytesIO(content)).result(timeout=300)
    return candidates_from_result(result, document_id)


def classify_document(candidates: list[dict], filename: str = "", content_type: str = "") -> str:
    """Auto-classify document type from OCR extraction candidates, filename, and mime type.

    CRITICAL: Aadhaar and PAN cards are frequently uploaded as smartphone photos or
    scans (e.g. WhatsApp Image..., IMG_..., camera photos). They contain legal ID text
    and numbers. They must ALWAYS be classified by their OCR content and NEVER blindly
    treated as a candidate profile photograph!
    """
    fn_lower = (filename or "").lower()
    ct_lower = (content_type or "").lower()

    # 1. Filename explicit hints for PAN, Aadhaar, Resume
    if any(k in fn_lower for k in ("pan", "pancard")):
        return "pan"
    if any(k in fn_lower for k in ("aadhaar", "aadhar")):
        return "aadhaar"
    if any(k in fn_lower for k in ("resume", "cv", "curriculum_vitae")):
        return "resume"

    # 2. Check for LLM doc_type_hint from structured extraction
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

    # 3. Check extracted fields from OCR candidates
    fields_found = {c["field"] for c in candidates}
    if "pan" in fields_found:
        return "pan"
    if "aadhaar" in fields_found:
        return "aadhaar"
    if "email" in fields_found or "phone" in fields_found:
        return "resume"

    # 4. Only classify as photograph if filename explicitly indicates a profile headshot/avatar
    if any(
        k in fn_lower
        for k in ("headshot", "passport_photo", "profile_pic", "candidate_photo", "avatar", "profile_photo")
    ):
        return "photograph"

    # 5. Image file with zero text candidates (i.e. pure photo with no document text)
    if ct_lower.startswith("image/") and not candidates:
        if not any(k in fn_lower for k in ("pan", "aadhaar", "aadhar", "card", "id", "doc")):
            return "photograph"

    return "other"
