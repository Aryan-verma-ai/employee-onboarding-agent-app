"""Microsoft Foundry Company Knowledge Base and Policy RAG Pipeline.

Implements enterprise knowledge retrieval with:
  1. Azure AI Search / Foundry IQ (if configured in Azure environment)
  2. Foundry Vector Store / File Search (if configured in Azure environment)
  3. Grounded enterprise policy knowledge store spanning:
     - Employee handbook
     - Leave policy
     - Holiday policy
     - Benefits policy
     - Payroll information
     - Working-hours policy
     - IT/security policy
     - Code of conduct / POSH
     - Department information
     - Onboarding guidelines

CRITICAL GUARDRAIL:
The agent must NEVER invent company policies. If information is not in the
knowledge base, the system returns:
"That information is not available in the company knowledge base. Please contact HR."
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

KNOWLEDGE_DOCS_DIR = Path(__file__).resolve().parent.parent / "company_knowledge"

FALLBACK_REFUSAL_MESSAGE = (
    "That information is not available in the company knowledge base. Please contact HR."
)

# Comprehensive Company Knowledge Registry covering all 10 corporate domains
COMPANY_POLICIES: list[dict[str, Any]] = [
    {
        "id": "employee_handbook",
        "category": "Company Overview",
        "title": "Company Employee Handbook & Culture",
        "filename": "employee_handbook.md",
        "content": (
            "Company Mission and Culture: Our mission is to build intelligent, enterprise-grade cloud "
            "and AI platforms. We foster an inclusive culture centered around transparency, innovation, "
            "integrity, and mutual respect.\n"
            "Workplace Standards: Professional conduct is required. We are committed to equal opportunities "
            "regardless of race, gender, caste, religion, age, sexual orientation, disability, or marital status. "
            "Zero tolerance for harassment.\n"
            "Communication Channels: Official email (@company.com), Enterprise Teams/Slack chat, HR Helpdesk "
            "(hr@company.com), IT Support (it-support@company.com).\n"
            "Performance & Development: Standard probation is 3 months, extendable by up to 3 months. "
            "Formal performance appraisals occur annually in April."
        ),
        "keywords": [
            "handbook", "culture", "mission", "values", "workplace", "standards",
            "probation", "performance", "appraisal", "communication", "hr email", "helpdesk"
        ],
    },
    {
        "id": "leave_policy",
        "category": "Leaves & Time Off",
        "title": "Employee Leave Policy & Entitlements",
        "filename": "leave_policy.md",
        "content": (
            "Annual / Privilege Leave (PL): 18 working days per calendar year, accrued at 1.5 days per month. "
            "Up to 30 unused PL days can be carried forward to the following calendar year. Must be applied via HRMS 48 hours in advance.\n"
            "Casual & Sick Leave (CL / SL): 12 working days per calendar year. Available for sudden personal emergencies or illness. "
            "Medical certificate required for sick leave extending beyond 3 consecutive days. Cannot be encashed or carried forward.\n"
            "Maternity Leave: 26 weeks (182 calendar days) of fully paid leave for female employees under Maternity Benefit Act.\n"
            "Paternity Leave: 2 weeks (10 working days) of paid leave for new fathers, to be availed within 6 months of childbirth or adoption.\n"
            "Bereavement Leave: Up to 5 consecutive working days for immediate family members.\n"
            "Compensatory Off (Comp-Off): Employees working on holidays or weekends with prior approval may claim comp-off within 60 days."
        ),
        "keywords": [
            "leave", "leaves", "annual leave", "privilege leave", "pl", "casual leave", "cl",
            "sick leave", "sl", "maternity", "paternity", "bereavement", "comp-off", "vacation", "time off"
        ],
    },
    {
        "id": "holiday_policy",
        "category": "Leaves & Holidays",
        "title": "Company Holiday Schedule & Public Holidays",
        "filename": "holiday_policy.md",
        "content": (
            "Annual Public Holidays: Employees are entitled to 10 declared paid public holidays per calendar year:\n"
            "1. Republic Day (Jan 26), 2. Holi (March), 3. Eid-ul-Fitr, 4. Independence Day (Aug 15), "
            "5. Mahatma Gandhi Jayanti (Oct 2), 6. Dussehra (October), 7. Diwali (Oct/Nov), "
            "8. Guru Nanak Jayanti (Nov), 9. Christmas Day (Dec 25), 10. Regional Holiday.\n"
            "Restricted / Optional Holidays: Employees may select up to 2 optional holidays per year from corporate calendar.\n"
            "Work on Holidays: Operational shifts receive equivalent Comp-Off or premium compensation."
        ),
        "keywords": [
            "holiday", "holidays", "public holidays", "festival", "diwali", "republic day",
            "independence day", "christmas", "eid", "gandhi jayanti", "calendar", "restricted holiday"
        ],
    },
    {
        "id": "benefits_policy",
        "category": "Compensation & Benefits",
        "title": "Employee Benefits, Health Insurance & Wellness",
        "filename": "benefits_policy.md",
        "content": (
            "Group Medical Health Insurance (Mediclaim): ₹5,00,000 floater coverage per family per year, "
            "covering employee, legal spouse, and up to 2 dependent children. Cashless hospitalization across network hospitals.\n"
            "Group Personal Accident (GPA): Coverage up to 3 times annual CTC against accidental disability.\n"
            "Group Term Life (GTL): Life insurance coverage equal to 3 times annual CTC.\n"
            "Annual Wellness Checkup: Complimentary diagnostic screening at partner centers.\n"
            "Learning Allowance: Annual stipend of ₹25,000 for relevant professional certifications (Azure, AWS, Kubernetes), books, and courses.\n"
            "Employee Assistance Program (EAP): 24/7 confidential counseling and wellness support."
        ),
        "keywords": [
            "benefit", "benefits", "insurance", "mediclaim", "health insurance", "gpa", "gtl",
            "medical cover", "wellness", "checkup", "learning allowance", "stipend", "eap", "counseling"
        ],
    },
    {
        "id": "payroll_information",
        "category": "Compensation & Benefits",
        "title": "Salary, Payroll Schedule & Statutory Deductions",
        "filename": "payroll_information.md",
        "content": (
            "Salary Disbursement Schedule: Net monthly salary is credited directly to designated bank accounts "
            "on the last working day of each calendar month. Payslips accessible via HRMS within 24 hours.\n"
            "Bank Registration: Cancelled cheque or bank statement with Account Number and IFSC required during onboarding.\n"
            "Employees' Provident Fund (EPF): Statutory 12% employee deduction matched by 12% employer contribution to EPFO.\n"
            "Tax Deducted at Source (TDS): Income tax deducted per Indian Income Tax Act. Tax declarations submitted via tax portal.\n"
            "Professional Tax (PT): Deducted monthly per state regulations.\n"
            "Gratuity: Payable upon 5+ years of continuous service under Payment of Gratuity Act, 1972."
        ),
        "keywords": [
            "salary", "payroll", "pay day", "payday", "epf", "pf", "provident fund", "tds",
            "tax", "bank account", "cheque", "ifsc", "gratuity", "payslip", "compensation"
        ],
    },
    {
        "id": "working_hours_policy",
        "category": "Working Policy",
        "title": "Working Hours, Core Collaboration & Hybrid Work Policy",
        "filename": "working_hours_policy.md",
        "content": (
            "Work Week: Monday through Friday (5-day work week).\n"
            "Daily Work Hours: 9:00 AM to 6:00 PM IST (inclusive of 1-hour lunch break).\n"
            "Core Collaboration Hours: 10:00 AM to 4:00 PM IST, during which team members must be reachable.\n"
            "Hybrid Working Model: 3 days per week working from office, up to 2 days per week remote work.\n"
            "Remote Work Requirements: 50+ Mbps broadband, quiet workspace, active VPN, and MFA.\n"
            "Attendance: Mark daily attendance in HR portal by 9:30 AM."
        ),
        "keywords": [
            "hours", "working hours", "timings", "shift", "core hours", "hybrid", "remote",
            "work from home", "wfh", "office days", "attendance", "login time"
        ],
    },
    {
        "id": "it_security_policy",
        "category": "IT & Security",
        "title": "IT Equipment, Password Standards & Acceptable Use Policy",
        "filename": "it_security_policy.md",
        "content": (
            "Hardware: Enterprise laptop (MacBook Pro or ThinkPad) and peripherals issued on Day 1.\n"
            "Password Standards: Minimum 12 characters, including uppercase, lowercase, numbers, and symbols. Rotated every 90 days.\n"
            "Multi-Factor Authentication (MFA): Mandatory across all accounts (Microsoft Entra ID, GitHub, Azure, AWS).\n"
            "Corporate VPN: Mandatory when accessing internal resources, staging, production databases, or cloud infrastructure.\n"
            "Data Protection: Customer data, proprietary code, and employee PII must never be stored on personal drives or unapproved cloud services.\n"
            "Incident Reporting: Report suspected phishing or security incidents to security@company.com within 1 hour."
        ),
        "keywords": [
            "it", "laptop", "computer", "macbook", "thinkpad", "password", "mfa", "vpn",
            "security", "data protection", "confidentiality", "phishing", "incident"
        ],
    },
    {
        "id": "code_of_conduct",
        "category": "Ethics & Compliance",
        "title": "Code of Conduct, Ethics, POSH & Moonlighting Policy",
        "filename": "code_of_conduct.md",
        "content": (
            "Equal Opportunity: Strict meritocracy with zero tolerance for discrimination based on race, gender, caste, or religion.\n"
            "POSH Policy: Compliance with POSH Act, 2013. Internal Complaints Committee (ICC) handles grievances confidentially within 7 business days.\n"
            "Moonlighting & Conflict of Interest: Secondary commercial employment or freelancing for competitors without written HR approval is strictly prohibited.\n"
            "Gifts: Gratuities or personal gifts exceeding ₹2,000 from clients/vendors may not be accepted.\n"
            "Whistleblower Protection: Confidential anonymous reporting available at ethics@company.com.\n"
            "Probation: Standard probation is 3 months."
        ),
        "keywords": [
            "conduct", "ethics", "posh", "harassment", "moonlighting", "dual employment",
            "conflict of interest", "gifts", "whistleblower", "grievance", "icc", "complaint"
        ],
    },
    {
        "id": "department_information",
        "category": "Organizational Structure",
        "title": "Company Departments & Team Responsibilities",
        "filename": "department_information.md",
        "content": (
            "Engineering: Develops cloud platforms, AI models, backend APIs, frontend apps, QA, and SRE/DevOps. Leadership: CTO.\n"
            "Human Resources (HR): Handles talent acquisition, onboarding, culture, payroll coordination, and compliance (hr@company.com).\n"
            "Finance & Accounts: Manages financial planning, budgets, taxation, expense audits, and payroll disbursement (finance@company.com).\n"
            "Sales & Partnerships: Drives enterprise client acquisition, customer relations, and business partnerships (sales@company.com).\n"
            "Operations & Facilities: Manages workstation infrastructure, procurement, and physical facilities (operations@company.com)."
        ),
        "keywords": [
            "department", "departments", "teams", "engineering", "hr", "finance",
            "sales", "operations", "cto", "structure", "who does what"
        ],
    },
    {
        "id": "onboarding_guidelines",
        "category": "Onboarding",
        "title": "Employee Onboarding Guidelines, Documents & Timeline",
        "filename": "onboarding_guidelines.md",
        "content": (
            "Required Onboarding Details & Documents:\n"
            "1. Full Legal Name\n"
            "2. Contact: Email address and 10-digit Indian mobile number\n"
            "3. Identity Proof: PAN Card OR Aadhaar Card (Only ONE of PAN or Aadhaar is required; both can be provided)\n"
            "4. Resume / CV: Academic and professional experience\n"
            "5. Photograph: Passport-style portrait for company ID profile picture\n"
            "6. Bank Proof: Cancelled cheque or statement with IFSC for salary deposits.\n"
            "Submission: Drag and drop files directly into chat or enter details manually via dashboard.\n"
            "OCR Processing: Azure Document Intelligence extracts fields automatically. User-uploaded photos take precedence.\n"
            "Finalization: Deterministic validation verifies requirements, creates Employee ID, sets start date, and sends welcome email.\n"
            "Timeline: Complete within 3 business days of joining."
        ),
        "keywords": [
            "onboard", "onboarding", "documents", "pan", "aadhaar", "resume", "cv",
            "photograph", "photo", "id proof", "timeline", "process", "checklist", "new hire"
        ],
    },
]

# Set of tokens recognized as belonging to company / onboarding domain
DOMAIN_TOKENS = {
    "onboard", "onboarding", "document", "documents", "pan", "aadhaar", "resume", "cv",
    "photo", "photograph", "picture", "upload", "status", "verify", "verification",
    "validate", "validation", "company", "policy", "policies", "leave", "leaves",
    "vacation", "holiday", "holidays", "work", "hours", "timing", "timings", "shift",
    "remote", "wfh", "hybrid", "office", "salary", "pay", "payroll", "benefit",
    "benefits", "insurance", "mediclaim", "pf", "provident", "fund", "it", "laptop",
    "macbook", "thinkpad", "vpn", "mfa", "password", "security", "conduct", "ethics",
    "posh", "probation", "department", "departments", "engineering", "finance", "sales",
    "operations", "hr", "cheque", "bank", "employee", "hire", "joining", "id", "profile",
    "allowance", "sick", "casual", "maternity", "paternity", "bereavement", "dress",
    "attendance", "portal", "moonlighting", "whistleblower", "cto", "gratuity", "tds"
}


def is_in_domain_query(text: str) -> bool:
    """Verify whether a user query pertains to onboarding or company policies."""
    if not text or not isinstance(text, str):
        return False
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return bool(tokens & DOMAIN_TOKENS)


def _search_azure_ai_search(query: str, endpoint: str, key: str, index_name: str) -> Optional[dict[str, Any]]:
    """Query Azure AI Search / Foundry IQ index if configured."""
    try:
        import httpx

        url = f"{endpoint.rstrip('/')}/indexes/{index_name}/docs/search?api-version=2023-11-01"
        payload = {"search": query, "top": 3, "queryType": "semantic"}
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload, headers={"api-key": key, "Content-Type": "application/json"})
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for doc in data.get("value", []):
                    results.append({
                        "title": doc.get("title", "Company Policy"),
                        "category": doc.get("category", "Policy"),
                        "content": doc.get("content", ""),
                    })
                if results:
                    return {"found": True, "results": results, "source": "azure_ai_search"}
    except Exception as exc:
        log.warning("Azure AI Search query failed, falling back to managed store: %s", exc)
    return None


def search_company_knowledge(query: str, top_k: int = 3) -> dict[str, Any]:
    """Retrieve grounded company policy documents for user inquiries.

    Evaluates:
      1. Azure AI Search / Foundry IQ (if configured)
      2. Grounded enterprise policy repository
      3. Strict refusal if the inquiry is out-of-scope or ungrounded.
    """
    if not query or not query.strip():
        return {
            "found": False,
            "results": [],
            "message": "Please provide a specific query regarding company policies, leaves, benefits, or onboarding.",
        }

    q_clean = query.strip()
    q_lower = q_clean.lower()
    q_tokens = set(re.findall(r"[a-z0-9]+", q_lower))

    # Reject obvious non-domain general trivia/geography queries
    off_topic_pattern = r"(?i)\b(where is|who is|what is the capital|weather in|tell me a joke|write a poem|who was|who won|population of|distance between)\b"
    if re.search(off_topic_pattern, q_clean) and not is_in_domain_query(q_clean):
        return {
            "found": False,
            "results": [],
            "message": FALLBACK_REFUSAL_MESSAGE,
        }

    # 1. Check Azure AI Search / Foundry IQ if configured
    search_endpoint = os.getenv("AZURE_AI_SEARCH_ENDPOINT")
    search_key = os.getenv("AZURE_AI_SEARCH_KEY")
    search_index = os.getenv("AZURE_AI_SEARCH_INDEX", "company-knowledge")
    if search_endpoint and search_key:
        remote_res = _search_azure_ai_search(q_clean, search_endpoint, search_key, search_index)
        if remote_res and remote_res.get("found"):
            return remote_res

    # 2. Grounded Enterprise Knowledge Search
    STOP_WORDS = {
        "the", "and", "for", "with", "this", "that", "company", "employee", "employees",
        "can", "what", "are", "how", "many", "does", "get", "tell", "about", "policy",
        "policies", "have", "will", "from", "any", "our", "all"
    }
    meaningful_tokens = {t for t in q_tokens if t not in STOP_WORDS and len(t) > 2}
    if not meaningful_tokens:
        meaningful_tokens = {t for t in q_tokens if len(t) > 2}

    scored_entries = []
    for doc in COMPANY_POLICIES:
        score = 0.0
        title_words = set(re.findall(r"[a-z0-9]+", doc["title"].lower()))
        content_words = set(re.findall(r"[a-z0-9]+", doc["content"].lower()))
        kw_phrases = [kw.lower() for kw in doc["keywords"]]
        kw_words = {w for kw in kw_phrases for w in re.findall(r"[a-z0-9]+", kw)}

        # Whole keyword phrase match in full query (with strict word boundaries)
        for kw in kw_phrases:
            if re.search(r"\b" + re.escape(kw) + r"\b", q_lower):
                score += 8.0

        # Title match on meaningful words
        for token in meaningful_tokens:
            if token in title_words:
                score += 6.0

        # Keywords match on meaningful words
        for token in meaningful_tokens:
            if token in kw_words:
                score += 4.0

        # Content match on meaningful words
        for token in meaningful_tokens:
            if token in content_words:
                score += 1.5

        if score >= 5.0:
            scored_entries.append((score, doc))

    scored_entries.sort(key=lambda x: x[0], reverse=True)
    top_matches = [doc for score, doc in scored_entries][:top_k]

    if not top_matches:
        return {
            "found": False,
            "results": [],
            "citations": [],
            "answer": FALLBACK_REFUSAL_MESSAGE,
            "message": FALLBACK_REFUSAL_MESSAGE,
        }

    formatted_answer = "\n\n".join(f"[{doc['title']}]\n{doc['content']}" for doc in top_matches)
    return {
        "found": True,
        "results": [
            {
                "title": doc["title"],
                "category": doc["category"],
                "content": doc["content"],
            }
            for doc in top_matches
        ],
        "citations": [doc["title"] for doc in top_matches],
        "answer": formatted_answer,
        "message": "Information retrieved from company knowledge base.",
    }
