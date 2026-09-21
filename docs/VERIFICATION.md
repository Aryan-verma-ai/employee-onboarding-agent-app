# Verification evidence

- Live Foundry agent employee-onboarding version1, gpt-4.1-mini: reconnection verified; eight evaluation scenarios passed. Runner records raw model and guarded application outcomes separately.
- Same existing resource: live Document Intelligence prebuilt-read extracted all five fields from synthetic PDF and PNG. Sanitized result: OCR_FIXTURE_RESULTS.json.
- Real local PostgreSQL: migration/RLS owner/HR/cross-tenant isolation, concurrent idempotent finalization, transaction rollback, and session advisory lock tests.
- Regression coverage includes consent withdrawal and checks after Foundry conversation persistence/tool commits, durable job lease fencing/recovery and SSO configuration/pagination.
- Full test count and output are recorded in the delivered outputs/test-results.txt. CI runs PostgreSQL tests using its service container.
- No live Blob/Defender or Entra interactive tenant sign-in acceptance has been claimed. See DEPLOYMENT.md for exact remaining configuration.
