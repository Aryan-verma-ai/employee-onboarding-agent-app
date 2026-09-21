# Production setup: existing subscription and resource group

Do not recreate Foundry. Existing agent/OCR already work. Storage accounts belong to subscriptions/resource groups; Entra app registrations belong to a tenant. Verify subscription47e1bcaf-28a0-466f-9f7b-d32d7289eebe and tenanta8780332-fecd-4f97-81b8-3782131af31a are selected before creating missing components. Use existing rg-richakadian077-2335.

## Inputs needed first

- Monthly spending ceiling in INR and whether this is a short demonstration or an always-on production deployment. Storage, Defender scanning, database and hosting have their own charges; do not assume Foundry credits cover every service.
- Email/object ID of the first HR operator, plus a separate normal employee test account if available. These are public identity identifiers, not passwords.
- Existing hosting/domain preference, if any. Otherwise plan a managed container host with HTTPS; its generated hostname determines the final Entra callback URI. An always-running OCR worker and PostgreSQL database are needed alongside the API.

## Portal configuration sequence

1. Storage accounts > Create: existing subscription/resource group, Korea Central if supported for the selected features; Standard general-purpose v2, LRS for initial cost sizing, secure transfer/TLS1.2+, anonymous blob access disabled. Create private container onboarding-private. Record Blob service endpoint as AZURE_STORAGE_ACCOUNT_URL; container as AZURE_STORAGE_CONTAINER. Keep hierarchical namespace off for the initial blob-index-tag integration. Configure approved network access from the host, not public anonymous access.
2. Enable Defender for Storage on-upload malware scanning and result blob index tags. Set a reviewed monthly scan-volume cap; exceeding it must leave unscanned uploads blocked. Runtime identity needs blob read/write and scan-tag read, not tag write. Do not grant employees storage credentials. Prefer a scoped custom runtime role so application compromise cannot assert a clean scan. Owner/admin must configure the Defender scanner identity and required permissions.
3. App registrations > New registration: employee-onboarding-api, single tenant. Record Application(client) ID as ENTRA_AUDIENCE. Expose API identifier api://<API-client-id>, delegated scope access_as_user; configure api.requestedAccessTokenVersion=2. Add enabled app role Onboarding.HR, allowed member type Users. Create its service principal/enterprise application if needed and assign HR role to the chosen operator.
4. Register employee-onboarding-web, single tenant, as a Single-page application. Record its client ID as ENTRA_SPA_CLIENT_ID. Add exact deployed https://<app-host>/auth/callback SPA redirect. Add delegated permission to the API access_as_user scope; grant consent under tenant policy. Set ENTRA_API_SCOPE=api://<API-client-id>/access_as_user, ENTRA_REDIRECT_URI and ENTRA_TENANT_ID. No SPA client secret required. Do not substitute the SPA client ID for the API audience.
5. Configure managed hosting identity for private storage and existing Foundry/OCR access. Provision approved PostgreSQL with TLS and separate migration/runtime roles. Put connection credentials in secure deployment configuration/local ignored .env. Run Alembic migrations, then deploy existing API and tenant-scoped worker. Set WORKER_TENANT_ID to the Entra tenant and WORKER_SUBJECT to an explicit service identifier. Set ENVIRONMENT=production and ALLOW_DEV_AUTH=false only when deployment settings are ready.
6. Run python -m scripts.production_check --env-file .env. This reveals missing variable names only, not secret values. Then verify real sign-in/HR role/normal-user denial, scan-gated upload, OCR, review, validation, finalization/receipt, restart recovery and cross-account isolation. A passing configuration inventory is not live production acceptance.

## What to send back

Nonsecret values: Blob endpoint/container, both app client IDs, scope, final HTTPS app hostname/callback, HR account email/object ID. Save DB credentials and resource keys in .env or deployment secret storage; never paste them into chat. If registration/role assignment is denied, a tenant administrator must perform that specific action; repeated login attempts will not repair missing privileges.

## References

- https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app
- https://learn.microsoft.com/en-us/entra/identity-platform/web-api-tutorial-01-register-app
- https://learn.microsoft.com/en-us/azure/defender-for-cloud/defender-for-storage-introduction
- https://learn.microsoft.com/en-us/azure/defender-for-cloud/understand-malware-scan-results
