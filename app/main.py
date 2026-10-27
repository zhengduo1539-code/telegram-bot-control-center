"""FastAPI lifecycle and health endpoints for Render."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.bot.service import BotService

SERVICE_NAME = "Animated Custom Emoji & Sticker Generator"
service = BotService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await service.start()
    app.state.bot_service = service
    try:
        yield
    finally:
        await service.stop()


app = FastAPI(title=SERVICE_NAME, version="3.0.0", lifespan=lifespan)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health() -> JSONResponse:
    body = {"service": SERVICE_NAME, "status": service.status, "telegram_ready": service.ready}
    return JSONResponse(body, status_code=200 if service.ready else 503)


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz() -> JSONResponse:
    # Liveness remains independent of BOT_TOKEN while the process starts.
    return JSONResponse({"status": "ok", "service": SERVICE_NAME})
