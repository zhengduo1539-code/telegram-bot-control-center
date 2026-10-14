# Telegram Bot Control Center — Retired

The Control Center has been retired and no longer monitors, connects to, or controls any Telegram bot.

The Render service is retained only to display a retirement notice. Its `/healthz` endpoint remains available for Render's own service health probe; it does not contact bot services.

## Independent bot operation

Each Telegram bot continues to run and be managed independently through its own existing code and hosting service. This repository is not part of their runtime or deployment path.

## Deployment

The service uses Python and FastAPI:

    pip install -r requirements.txt
    uvicorn main:app --host 0.0.0.0 --port $PORT

The root page returns HTTP 410 (Gone) with the retirement notice. Other application paths also return HTTP 410. Only `/healthz` remains available to report this retired service's health.

The former bot health-check URLs and dashboard credentials are no longer required.
