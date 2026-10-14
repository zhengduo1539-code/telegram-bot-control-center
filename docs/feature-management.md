# Per-bot feature management

The dashboard keeps service health monitoring separate from each bot's feature workspace. The browser only talks to the Control Center; the Control Center proxies requests server-to-server to the bot APIs.

## Current supported scope

| Bot | Readable capabilities | Writable settings |
| --- | --- | --- |
| Z28 Attendance Bot | Current global activity limits; activity reminder state | Global activity duration limits (eat, wc, smoke, wcd); activity reminder enabled/disabled |
| Admin Bot | Calculator capability, usage statistics, MongoDB connection state | None; this bot currently has no persistent calculator settings |
| M58 Attendance Bot | Current activity limits | Activity duration limits (eat, wc, smoke, wcd) |

The Control Center intentionally does not expose controls for features that do not exist in a bot's current backend.

## Required secret configuration

Generate one high-entropy random secret and set the exact same value as CONTROL_CENTER_API_TOKEN in Render Environment Variables for:
- telegram-bot-control-center
- Z28-Telegram-Attendance-Bot
- admin_bot
- M58-Telegram-Attendance-Bot

Never put the production secret in GitHub, .env.example, browser JavaScript, or a URL. The bot APIs reject requests when the secret is absent or invalid. Until the variable is configured on all four services and the bot API changes are deployed, the workspace should show an integration error rather than pretending a setting was saved.

The Control Center may also use Z28_CONTROL_CENTER_URL, ADMIN_BOT_CONTROL_CENTER_URL, and M58_CONTROL_CENTER_URL to override the default backend URLs. These are server-side values.

## API contract

- Z28: GET /api/control-center/features, PUT /api/control-center/activity-limits, PUT /api/control-center/activity-reminders
- M58: GET /api/control-center/features, PUT /api/control-center/activity-limits
- Admin Bot: GET /control-center/features (read-only)

All requests use x-control-center-token. Writes validate the activity type and require a whole-minute duration from 1 to 1440. Existing attendance services and stores remain the source of truth; browser local storage is not used for settings.

## Release checklist

1. Review the draft PRs in all four repositories.
2. Run the existing Python tests and each Node API server's pnpm --filter @workspace/api-server run typecheck / build.
3. Configure the shared secret in Render without committing it.
4. Merge bot API PRs, verify each health endpoint, then merge the Control Center PR.
5. Open each bot's Feature Management workspace and confirm values load from the backend. Save a non-disruptive setting and re-read it to verify persistence before treating the integration as production-ready.