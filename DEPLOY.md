# Deploy Schedule Bot to Render

## 1. Telegram setup

1. Create a bot with @BotFather.
2. Save the bot token as `BOT_TOKEN` in Render.
3. Add the bot to the target group or channel.
4. Give the bot permission to send messages. For channels, grant **Post Messages**.

## 2. MongoDB setup

1. Create a MongoDB Atlas database and a database user with read/write access.
2. Configure Network Access for the Render service. Prefer a restricted allowlist where your hosting network permits it.
3. Save the connection string as `MONGODB_URI`.
4. Set `MONGODB_DATABASE=schedule_bot`.

## 3. Render service

Connect this GitHub repository as a Render Web Service.

| Setting | Value |
|---|---|
| Runtime | Python |
| Build command | `pip install -r requirements.txt` |
| Start command | `python main.py` |
| Health check path | `/healthz` |
| Instance count | 1 |

Configure `BOT_TOKEN`, `MONGODB_URI`, `ADMIN_IDS` and optionally `MONGODB_DATABASE` and `SCHEDULER_INTERVAL_SECS`. Render supplies `PORT`.

## 4. Verify the service

- `/healthz` should return HTTP 200 for liveness.
- `/health` should return HTTP 200 after MongoDB and APScheduler are ready.
- In Telegram, send `/start` and `/ping`.
- Render logs should show MongoDB connected and APScheduler started.

## 5. Verify Daily Queue

1. Open **Manage Queue** in the bot's private chat.
2. Choose **Set Daily Slots**, select a target chat, enter times in `HH:MM` format and choose the timezone.
3. Choose **Add Content to Queue**, send text or supported media and confirm.
4. The confirmation shows the next posting slot. **View Queue** shows queued, posted and failed content.
5. Verify the message appears in the target group/channel at the configured time.

Run exactly one Render instance for this polling bot. Do not commit real tokens or database credentials.
