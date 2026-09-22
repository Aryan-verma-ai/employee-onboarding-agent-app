# Final demonstration runbook

## Live endpoint

- HR dashboard: `https://eonbdemo922app.icyflower-fd71ac7e.koreacentral.azurecontainerapps.io/hr`
- Health: `/health`
- Readiness: `/ready`

The app runs as an Azure Container Apps Consumption workload with `minReplicas=0`. Its first request after idle can take a few minutes because the current Azure for Students environment cannot build the supplied Dockerfile through ACR Tasks; the app installs its pinned dependencies during that cold start. Leave the dashboard open until it responds, then subsequent demonstrations are responsive while the replica is warm.

## Demonstration flow

1. Sign in with the configured HR Microsoft Entra operator account.
2. Open the HR queue and create a consented synthetic onboarding case.
3. Upload only synthetic PDF, PNG, or JPEG documents. Review extraction candidates, correct fields, validate, and use the explicit HR finalization flow.
4. Show the confirmation receipt, queue filters, and CSV/XLSX export. Do not enter real PAN, Aadhaar, or employee documents for this temporary demonstration.

## Verified on 2026-09-22

- Public `/health`: HTTP 200.
- Public `/ready`: HTTP 200.
- The Azure revision is healthy after its temporary database credential was repaired.
- Automated suite: 83 passed, 4 PostgreSQL-only tests skipped because no dedicated `TEST_POSTGRES_URL` was supplied locally.

## Cost and cleanup

Keep the Container App at `minReplicas=0` between demonstrations. The PostgreSQL server and Defender scanning may incur charges while enabled. After the final demo, stop or delete the PostgreSQL server and Container App resources using the resource group, retaining only resources the owner wishes to keep. Never commit or package keys, database passwords, tokens, `.env`, or Azure CLI credential caches.
