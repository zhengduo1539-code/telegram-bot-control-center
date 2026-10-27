"""FastAPI lifecycle, health probes and Telegram webhook endpoint."""
from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.bot.service import BotService


SERVICE_NAME = "Animated Emoji Analyzer & Rebuilder"
logger = logging.getLogger(__name__)
service = BotService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await service.start()
    app.state.bot_service = service
    try:
        yield
    finally:
        await service.stop()


app = FastAPI(title=SERVICE_NAME, version="1.0.0", lifespan=lifespan)


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz() -> JSONResponse:
    # Liveness must not fail while the service is waiting for environment configuration.
    return JSONResponse({"status": "ok", "service": SERVICE_NAME})


@app.api_route("/health", methods=["GET", "HEAD"])
async def health() -> JSONResponse:
    status_code = 200 if service.ready else 503
    return JSONResponse(
        {
            "service": SERVICE_NAME,
            "status": service.status,
            "ready": service.ready,
            "mode": service.mode,
            "details": service.reason,
        },
        status_code=status_code,
    )


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    if service.mode != "webhook" or not service.ready or not service.webhook_secret:
        raise HTTPException(status_code=503, detail="Telegram webhook is not ready.")
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        x_telegram_bot_api_secret_token, service.webhook_secret
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook secret.")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON update.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Update must be a JSON object.")
    try:
        await service.process_webhook(payload)
    except RuntimeError as exc:
        logger.warning("Webhook received while bot service was not ready.")
        raise HTTPException(status_code=503, detail="Telegram bot is not ready.") from exc
    return {"ok": True}
