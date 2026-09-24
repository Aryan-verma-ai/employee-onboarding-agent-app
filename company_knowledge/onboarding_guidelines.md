# Company Employee Onboarding Guidelines

This document provides complete instructions for new hires navigating their onboarding journey.

## 1. Onboarding Checklist
To complete onboarding smoothly, all new hires must provide the following:
1. **Full Legal Name**: As printed on government tax records.
2. **Contact Details**: Personal candidate email address and active 10-digit mobile number.
3. **Identity Proof**:
   - PAN Card (Permanent Account Number) OR Aadhaar Card (Unique Identification Authority of India).
   - Only ONE of PAN or Aadhaar is required to fulfill the statutory identity proof requirement.
4. **Resume / CV**: Outlining work experience and academic background.
5. **Passport-Style Photograph**: For the official employee profile picture and company ID badge.
6. **Bank Account Proof**: Cancelled cheque or bank statement showing account number and IFSC code for salary deposits.

## 2. Document Submission Methods
- **AI-Driven Upload**: Drag and drop your documents (PDF, PNG, JPEG) directly into the Onboardly agent workspace chat or upload zone.
- **Manual Entry**: Edit fields directly on the dashboard if you prefer manual entry over document scans.

## 3. Automated OCR & Extraction
- Upon document upload, Azure Document Intelligence and our extraction pipeline securely scan the document:
  - Resumes: Extracts candidate Name, Email, and Phone number.
  - PAN Card: Extracts PAN number, Full Name, and Date of Birth.
  - Aadhaar Card: Extracts Aadhaar number, Full Name, Address, and Date of Birth.
  - ID Cards / Photos: Automatically crops the portrait headshot for your company ID picture, or accepts a user-uploaded photo which takes priority.
- Scanned files are scanned for malware using Microsoft Defender for Storage before processing.

## 4. Validation & Finalization
- Once the required fields are populated and consent is confirmed, validation executes deterministic checks.
- When validation passes, the system:
  1. Creates the active Employee record in PostgreSQL.
  2. Generates your unique Employee ID (e.g., `EMP-XXXXXX`).
  3. Assigns your official start working date.
  4. Automatically dispatches the official Welcome Congratulations Email.
- The onboarding journey is completed within 3 business days of joining.
