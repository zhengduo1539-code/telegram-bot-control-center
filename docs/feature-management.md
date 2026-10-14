# Per-bot feature management

The dashboard keeps service health monitoring separate from each bot's feature workspace. The browser only talks to the Control Center; the Control Center proxies requests server-to-server to the bot APIs.

## Current supported scope

| Bot | Readable capabilities | Writable settings |
| --- | --- | --- |
| Z28 Attendance Bot | Current global activity limits; activity reminder state | Global activity duration limits (eat, wc, smoke, wcd); activity reminder enabled/disabled |
| Admin Bot | Calculator capability, usage statistics, MongoDB connection state | None; this bot currently has no persistent calculator settings |

The Control Center intentionally does not expose controls for features that do not exist in a bot's current backend.

## Required secret configuration

Generate one high-entropy random secret and set the exact same value as CONTROL_CENTER_API_TOKEN in Render Environment Variables for:
- telegram-bot-control-center
- Z28-Telegram-Attendance-Bot
- admin_bot

Never put the production secret in GitHub, .env.example, browser JavaScript, or a URL. The bot APIs reject requests when the secret is absent or invalid. Until the variable is configured on all three services and the bot API changes are deployed, the workspace should show an integration error rather than pretending a setting was saved.

The Control Center may also use Z28_CONTROL_CENTER_URL and ADMIN_BOT_CONTROL_CENTER_URL to override the default backend URLs. These are server-side values.

## API contract

- Z28: GET /api/control-center/features, PUT /api/control-center/activity-limits, PUT /api/control-center/activity-reminders
- Admin Bot: GET /control-center/features (read-only)

All requests use x-control-center-token. Writes validate the activity type and require a whole-minute duration from 1 to 1440. Existing attendance services and stores remain the source of truth; browser local storage is not used for settings.

## Release checklist

1. Review the Z28, Admin Bot, and Control Center PRs.
2. Run the existing Python tests and Z28's pnpm --filter @workspace/api-server run build (the standalone typecheck currently has pre-existing repository errors).
3. Configure the shared secret in Render without committing it.
4. Merge bot API PRs, verify each health endpoint, then merge the Control Center PR.
5. Open the Z28 and Admin Bot Feature Management workspaces and confirm values load from the backend. Save a non-disruptive setting and re-read it to verify persistence before treating the integration as production-ready.