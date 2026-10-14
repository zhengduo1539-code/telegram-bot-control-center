# Telegram Bot Control Center

A private monitoring dashboard with separate, backend-backed feature workspaces for two Telegram bots:

- Z28 Attendance Bot (Node.js)
- Admin Bot (Python)

The dashboard checks each configured HTTP health endpoint and shows status, latency, and links to Render and GitHub.

## Scope

- Private dashboard with username/password authentication over HTTPS.
- Concurrent health checks with bounded timeouts.
- Responsive dark interface, manual refresh, and pausable 60-second auto-refresh.
- Browser-local response-time trend lines, observed availability estimates, and a recent status-change timeline.
- Export the latest health snapshot as CSV for offline review.
- Browser-local history is bounded and can be cleared; no monitoring samples are written to a server database.
- Public /healthz endpoint for Render's own health check.
- Separate feature-management workspace per bot; only existing backend features are exposed. Supported writes are Z28 activity duration limits and activity reminders. Admin Bot remains read-only because it has no persistent calculator settings.
- No restart, deploy, environment-variable, database, or other infrastructure controls.
- Feature-management APIs use a shared server-side CONTROL_CENTER_API_TOKEN; see docs/feature-management.md before enabling the integration.

A successful health endpoint means that endpoint returned HTTP 2xx; it does not prove every bot feature is functioning correctly.

## Run locally

Use Python 3.11 or newer.

    python -m venv .venv
    # Windows PowerShell: .venv\Scripts\Activate.ps1
    # macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt

Set environment variables before starting the app:

    # Windows PowerShell
    $env:DASHBOARD_USERNAME="owner"
    $env:DASHBOARD_PASSWORD="use-a-long-unique-password"
    uvicorn main:app --reload

Then open http://127.0.0.1:8000. Do not commit real credentials to GitHub.

## Deploy to Render

1. Create a new Web Service from this repository, or use render.yaml.
2. Set DASHBOARD_PASSWORD in Render environment settings to a strong, unique secret.
3. Confirm the username and both health endpoint URLs.
4. Deploy and open the generated HTTPS URL.
5. Verify /healthz returns {"status":"ok"} and sign in to confirm the dashboard loads.

Render free services may spin down when idle, so the first request can take longer.

## Configuration

| Variable | Purpose |
| --- | --- |
| DASHBOARD_USERNAME | Login username; defaults to owner |
| DASHBOARD_PASSWORD | Required secret; app fails closed if missing |
| Z28_HEALTH_URL | Z28 health endpoint |
| ADMIN_BOT_HEALTH_URL | Admin Bot health endpoint |
| HEALTH_CHECK_TIMEOUT | Per-endpoint timeout in seconds (1–20) |
| CONTROL_CENTER_API_TOKEN | Same high-entropy secret on the Control Center, Z28, and Admin Bot services |
| Z28_CONTROL_CENTER_URL | Optional override for Z28's feature API base URL |
| ADMIN_BOT_CONTROL_CENTER_URL | Optional override for Admin Bot's feature API base URL |

## Security notes

- Use HTTPS for any non-local deployment.
- Keep dashboard credentials in Render environment variables or a local untracked .env file.
- Browser authorization is held in sessionStorage for the current tab session; sign out to clear it.
- Monitored URLs are configured server-side and are not supplied by dashboard visitors.
- This version does not store Render API tokens and cannot trigger deployments or restarts.
