# Company IT & Information Security Policy

Information security and customer data protection are paramount. Every employee must strictly adhere to the following security standards.

## 1. Hardware & Asset Allocation
- **Company Devices**: Standard enterprise laptop (MacBook Pro or ThinkPad) and security peripherals are issued on Day 1.
- **Ownership**: All issued hardware and software remain company property and must be surrendered upon departure.
- **Personal Devices**: Personal laptops or unmanaged storage devices (USB flash drives, external HDDs) must never be connected to production environments.

## 2. Authentication & Access Management
- **Password Complexity**: Passwords must contain a minimum of 12 characters, including uppercase, lowercase, numbers, and symbols. Passwords must be rotated every 90 days.
- **Multi-Factor Authentication (MFA)**: Mandatory across all corporate identities (Microsoft Entra ID, GitHub, Azure portal, AWS).
- **Session Locking**: Workstations must be locked (`Win+L` or `Cmd+Ctrl+Q`) whenever leaving the desk unattended.

## 3. Network & Remote Security
- **Corporate VPN**: Mandatory for accessing internal staging servers, database clusters, and cloud management consoles.
- **Public Wi-Fi**: Connecting to unprotected public Wi-Fi without active corporate VPN is strictly prohibited.

## 4. Data Protection & Confidentiality
- Proprietary source code, customer records, and identity files (PAN/Aadhaar/PII) must never be shared on public forums, unapproved cloud drives (Google Drive, personal OneDrive), or unverified AI platforms.
- Role-Based Access Control (RBAC) and Least Privilege principles are enforced at all times.

## 5. Security Incident Reporting
- Any suspected phishing attempt, malware alert, or lost device must be reported immediately to `security@company.com` within 1 hour of discovery.
