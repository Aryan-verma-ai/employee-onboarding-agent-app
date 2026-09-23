"""Company knowledge base and RAG retrieval pipeline for employee onboarding."""

import re
from typing import Any

# Comprehensive Company Handbook and Onboarding Knowledge Base
COMPANY_KNOWLEDGE: list[dict[str, Any]] = [
    {
        "id": "onboarding_docs",
        "category": "Onboarding",
        "title": "Required Onboarding Documents & Verification",
        "content": (
            "All new employees must submit the following mandatory documents during onboarding:\n"
            "1. PAN Card: Mandatory for tax identification and payroll processing. Must match full legal name.\n"
            "2. Aadhaar Card: Mandatory government identity and address proof. 12-digit UID.\n"
            "3. Resume / Curriculum Vitae: Comprehensive work history and educational qualifications.\n"
            "4. Photograph: Recent passport-size photograph with white/plain background.\n"
            "5. Bank Account Details: Cancelled cheque or bank statement showing account number and IFSC code for salary deposits.\n"
            "Document format guidelines: PDF, PNG, or JPEG files. Scans must be legible with all corners visible.\n"
            "Automated OCR extracts your Name, Email, Phone, PAN number, and Aadhaar number upon upload.\n"
            "Once documents are uploaded and verified, HR conducts a final review to create your employee profile."
        ),
        "keywords": [
            "documents",
            "pan",
            "aadhaar",
            "resume",
            "photo",
            "photograph",
            "bank",
            "cheque",
            "upload",
            "id proof",
            "address proof",
            "verification",
        ],
    },
    {
        "id": "onboarding_workflow",
        "category": "Onboarding",
        "title": "Onboarding Process & Timeline",
        "content": (
            "The employee onboarding process consists of 4 main phases:\n"
            "Phase 1 - Welcome & Document Upload: The employee receives access to the onboarding workspace and uploads required documents.\n"
            "Phase 2 - OCR Extraction & Verification: Azure Document Intelligence extracts identity fields and populates the profile.\n"
            "Phase 3 - Validation: System deterministic rules check document presence and data format consistency.\n"
            "Phase 4 - HR Attestation & Finalization: An authorized HR representative verifies the submitted data, confirms accuracy, and generates the unique Employee ID.\n"
            "Timeline: The entire document submission process should be completed within 3 business days of joining."
        ),
        "keywords": [
            "process",
            "workflow",
            "steps",
            "timeline",
            "phases",
            "how to onboard",
            "joining",
            "new hire",
        ],
    },
    {
        "id": "work_hours",
        "category": "Working Policy",
        "title": "Working Hours, Attendance & Hybrid Work Policy",
        "content": (
            "Standard Work Hours: Monday through Friday, 9:00 AM to 6:00 PM IST (inclusive of a 1-hour lunch break).\n"
            "Core Collaboration Hours: 10:00 AM to 4:00 PM IST, during which all team members must be available for meetings.\n"
            "Hybrid Work Model: Employees are expected to work from the office 3 days a week and may work remotely 2 days a week.\n"
            "Remote Work Guidelines: High-speed internet connection (minimum 50 Mbps), quiet workspace, and adherence to security policies.\n"
            "Attendance Logging: Log into the HR employee portal daily by 9:30 AM to mark attendance."
        ),
        "keywords": [
            "hours",
            "timings",
            "work hours",
            "shift",
            "remote",
            "work from home",
            "wfh",
            "hybrid",
            "attendance",
            "office days",
        ],
    },
    {
        "id": "leave_policy",
        "category": "Leaves & Holidays",
        "title": "Employee Leave Policy & Holidays",
        "content": (
            "The company provides the following annual leave entitlements (pro-rated from joining date):\n"
            "1. Annual / Privilege Leave (PL): 18 days per calendar year. Can be carried forward up to a maximum of 30 days.\n"
            "2. Casual / Sick Leave (CL/SL): 12 days per calendar year. Available for illness or personal emergencies.\n"
            "3. Maternity Leave: 26 weeks of fully paid leave for female employees, as per statutory requirements.\n"
            "4. Paternity Leave: 2 weeks of paid leave for new fathers, to be availed within 6 months of childbirth.\n"
            "5. Bereavement Leave: Up to 5 days of compassionate leave for immediate family members.\n"
            "6. Public Holidays: 10 fixed company holidays per year, published annually in December.\n"
            "Leave Application: Must be applied through the HRMS portal at least 48 hours in advance for planned leaves."
        ),
        "keywords": [
            "leave",
            "leaves",
            "vacation",
            "holiday",
            "holidays",
            "sick leave",
            "casual leave",
            "maternity",
            "paternity",
            "time off",
        ],
    },
    {
        "id": "payroll_benefits",
        "category": "Compensation & Benefits",
        "title": "Salary, Payroll, PF & Health Insurance",
        "content": (
            "Compensation Structure & Payment Schedule:\n"
            "1. Pay Day: Salaries are credited directly to designated bank accounts on the last working day of each calendar month.\n"
            "2. Provident Fund (PF): 12% statutory employer and employee contribution to the Employees' Provident Fund Organisation (EPFO).\n"
            "3. Group Medical Insurance: Comprehensive health insurance of ₹5,00,000 covering employee, spouse, and up to 2 children.\n"
            "4. Accidental & Term Life Cover: Group personal accident insurance covered up to 3x annual CTC.\n"
            "5. Annual Wellness Checkup: Complimentary annual executive health checkup at partner diagnostics.\n"
            "6. Learning & Development Allowance: Annual stipend of ₹25,000 for relevant certifications, books, and courses."
        ),
        "keywords": [
            "salary",
            "payroll",
            "pay",
            "pf",
            "provident fund",
            "insurance",
            "mediclaim",
            "benefits",
            "health insurance",
            "allowance",
            "ctc",
        ],
    },
    {
        "id": "it_security",
        "category": "IT & Security",
        "title": "IT Equipment, Data Protection & Acceptable Use Policy",
        "content": (
            "IT Provisioning & Security Standards:\n"
            "1. Hardware: Company-owned laptop and peripherals are shipped or handed over on Day 1.\n"
            "2. Password Policy: Must be minimum 12 characters, including upper/lowercase, numbers, and special symbols. Rotate every 90 days.\n"
            "3. Multi-Factor Authentication (MFA): Mandatory on all enterprise accounts (Microsoft 365, GitHub, internal tools).\n"
            "4. VPN: Mandatory when connecting to internal staging, production databases, or cloud resources.\n"
            "5. Data Protection: Customer and company data must never be stored on personal drives or unapproved cloud services.\n"
            "6. Reporting Incidents: Immediately notify security@company.com of any suspected phishing or security breach."
        ),
        "keywords": [
            "laptop",
            "it",
            "computer",
            "macbook",
            "vpn",
            "mfa",
            "password",
            "security",
            "data",
            "confidentiality",
            "software",
        ],
    },
    {
        "id": "code_of_conduct",
        "category": "Company Policies",
        "title": "Code of Conduct, Ethics & POSH Policy",
        "content": (
            "Standards of Professional Conduct:\n"
            "1. Equal Opportunity: Zero tolerance for discrimination based on race, gender, religion, caste, sexual orientation, or disability.\n"
            "2. Prevention of Sexual Harassment (POSH): Strict policy in accordance with the POSH Act, 2013. Internal Complaints Committee (ICC) investigates grievances confidentially within 7 business days.\n"
            "3. Conflict of Interest: Moonlighting or taking secondary commercial employment without explicit written HR consent is strictly prohibited.\n"
            "4. Whistleblower Mechanism: Anonymous grievance reporting channel available at ethics@company.com.\n"
            "5. Probation Period: Standard probation period is 3 months, extendable by up to 3 additional months based on performance."
        ),
        "keywords": [
            "conduct",
            "ethics",
            "posh",
            "harassment",
            "discrimination",
            "probation",
            "rules",
            "complaint",
            "grievance",
            "moonlighting",
        ],
    },
    {
        "id": "departments",
        "category": "Departments",
        "title": "Company Departments & Organizational Structure",
        "content": (
            "The company operates five core functional departments:\n"
            "1. Engineering: Builds product platforms, cloud infrastructure, AI models, and enterprise software.\n"
            "2. HR (Human Resources): Handles talent acquisition, employee onboarding, culture, and benefits.\n"
            "3. Finance: Responsible for financial planning, taxation, payroll accounting, and statutory compliance.\n"
            "4. Sales: Drives client acquisition, enterprise partnerships, and revenue growth.\n"
            "5. Operations: Manages IT systems, office facilities, procurement, and vendor relations."
        ),
        "keywords": [
            "department",
            "departments",
            "engineering",
            "hr",
            "finance",
            "sales",
            "operations",
            "teams",
            "structure",
        ],
    },
]

# Keywords that indicate the query is within company / onboarding domain
DOMAIN_KEYWORDS = {
    "onboard",
    "onboarding",
    "document",
    "documents",
    "pan",
    "aadhaar",
    "resume",
    "cv",
    "photo",
    "photograph",
    "upload",
    "status",
    "verify",
    "verification",
    "validate",
    "validation",
    "company",
    "policy",
    "policies",
    "leave",
    "leaves",
    "vacation",
    "holiday",
    "holidays",
    "work",
    "hours",
    "timing",
    "timings",
    "shift",
    "remote",
    "wfh",
    "hybrid",
    "office",
    "salary",
    "pay",
    "payroll",
    "benefit",
    "benefits",
    "insurance",
    "mediclaim",
    "pf",
    "provident",
    "fund",
    "it",
    "laptop",
    "vpn",
    "mfa",
    "security",
    "conduct",
    "ethics",
    "posh",
    "probation",
    "department",
    "engineering",
    "finance",
    "sales",
    "operations",
    "hr",
    "cheque",
    "bank",
    "offer",
    "employee",
    "hire",
    "joining",
    "id",
    "profile",
    "allowance",
    "sick",
    "casual",
    "maternity",
    "paternity",
    "dress",
    "code",
    "attendance",
    "portal",
    "exit",
    "notice",
}


def is_in_domain_query(text: str) -> bool:
    """Check if the user query is related to company policies or employee onboarding."""
    if not text or not isinstance(text, str):
        return False
    # Clean tokens
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    if not tokens:
        return False
    # Check intersection with domain keywords
    return bool(tokens & DOMAIN_KEYWORDS)


def search_company_knowledge(query: str, top_k: int = 3) -> dict[str, Any]:
    """Search company policies and onboarding handbook using hybrid keyword and relevance scoring."""
    if not query or not query.strip():
        return {
            "found": False,
            "results": [],
            "message": "Please provide a specific query regarding company policies or onboarding.",
        }

    q_clean = query.lower()
    q_tokens = set(re.findall(r"[a-z0-9]+", q_clean))

    scored_entries = []
    for doc in COMPANY_KNOWLEDGE:
        score = 0.0
        # Title match
        title_lower = doc["title"].lower()
        if any(token in title_lower for token in q_tokens if len(token) > 2):
            score += 4.0

        # Keywords match
        for kw in doc["keywords"]:
            if kw in q_clean:
                score += 3.0
            elif any(token == kw for token in q_tokens):
                score += 2.0

        # Content match
        content_lower = doc["content"].lower()
        for token in q_tokens:
            if len(token) > 2 and token in content_lower:
                score += 1.0

        if score > 0:
            scored_entries.append((score, doc))

    scored_entries.sort(key=lambda x: x[0], reverse=True)
    top_matches = scored_entries[:top_k]

    if not top_matches:
        return {
            "found": False,
            "results": [],
            "message": "No specific company policy found matching your query. Please contact HR for further details.",
        }

    return {
        "found": True,
        "results": [
            {"title": doc["title"], "category": doc["category"], "content": doc["content"]}
            for score, doc in top_matches
        ],
    }
