# Microsoft Entra browser sign-in

The HR dashboard uses Microsoft MSAL Browser 4.27.0, bundled locally from the official `@azure/msal-browser` npm package. The package SHA-512 integrity was verified during vendoring; version/integrity and MIT license are in `app/static/MSAL-VERSION.txt` and `MSAL-LICENSE.txt`. No CDN is used.

Register a single-tenant SPA client in Entra and expose a delegated scope on the API application (for example `api://<API-client-id>/access_as_user`). Configure the API application to issue v2 access tokens. Grant that delegated scope to the SPA, grant tenant consent as required, and assign HR/Onboarding.HR API application roles to authorized HR users. The server checks API audience, issuer, tenant, signature, expiry and roles independently of the browser.

Set these deployment values:

- `ENTRA_TENANT_ID`: tenant UUID, also used by API verification.
- `ENTRA_AUDIENCE`: API application client ID, not the SPA client ID.
- `ENTRA_SPA_CLIENT_ID`: public SPA application client ID.
- `ENTRA_API_SCOPE`: fully qualified delegated scope beginning `api://`.
- `ENTRA_REDIRECT_URI`: exact HTTPS URL ending `/auth/callback`, registered as a **Single-page application** redirect URI in Entra. HTTP localhost is allowed only in explicit development mode.

No client secret belongs in browser configuration. `/api/auth/config` returns only the allowlisted public values, or configured=false. Missing SSO configuration disables sign-in visibly rather than enabling anonymous API access. Register `app.sso.router` in the application.

MSAL implements authorization code flow with PKCE and validates the sign-in transaction. Popup sign-in returns to the same application's blank callback page. Access/refresh tokens use MSAL memoryStorage; only temporary PKCE transaction data uses sessionStorage. Tokens are never stored in localStorage or manually pasted into the UI. Reloading requires sign-in again, using the existing Microsoft session where available. Application sign-out clears the MSAL cache and displayed records; it does not end other Microsoft sessions. API calls use Authorization Bearer, so no application session cookies or custom CSRF mechanism are introduced.

The dashboard queue uses deterministic offset/limit pages (25 per page). Concurrent additions may shift offset pages; refresh resets to page one. Exports include all matching rows up to the explicit 5,000 cap and reject larger sets with an instruction to narrow filters. They never silently truncate. Completed cases expose the authorized JSON confirmation download.

Validation: local configuration/security tests and HTTP pagination/export tests do not contact Entra. A deployment administrator must still register the real applications and verify popup sign-in, delegated API consent, normal-user access, HR roles, logout and token expiration in the deployed browser. No live tenant sign-in is claimed by unit tests.

Reference: https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow
