from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="Telegram Bot Control Center (Retired)",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

RETIRED_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>Control Center Retired</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0b1020; color: #edf1ff; }
    * { box-sizing: border-box; }
    body { min-height: 100vh; margin: 0; display: grid; place-items: center; padding: 24px; }
    main { width: min(100%, 620px); padding: clamp(24px, 5vw, 44px); border: 1px solid #29334f; border-radius: 22px; background: #111a2e; box-shadow: 0 24px 80px #0005; }
    .status { display: inline-flex; align-items: center; gap: 9px; padding: 7px 11px; border: 1px solid #735a37; border-radius: 999px; color: #ffd89a; font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: #f6b84b; }
    h1 { margin: 24px 0 12px; font-size: clamp(28px, 5vw, 40px); line-height: 1.12; letter-spacing: -.035em; }
    p { margin: 0; color: #b6c1da; font-size: 16px; line-height: 1.75; }
    .note { margin-top: 24px; padding-top: 20px; border-top: 1px solid #29334f; color: #8997b5; font-size: 13px; line-height: 1.65; }
  </style>
</head>
<body>
  <main>
    <span class="status"><span class="dot" aria-hidden="true"></span> Service retired</span>
    <h1>Control Center is no longer in use.</h1>
    <p>This dashboard no longer monitors, connects to, or controls any Telegram bot. Each bot continues to run independently in its own service.</p>
    <div class="note">This page is retained only to explain the retirement. Bot functionality and bot-specific dashboards are not managed from here.</div>
  </main>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def retired_home() -> HTMLResponse:
    return HTMLResponse(content=RETIRED_PAGE, status_code=410)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    # Keep Render's service health probe available; no bot endpoints are contacted.
    return {"status": "ok", "service": "control-center-retired"}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    response_class=HTMLResponse,
)
async def retired_endpoint(path: str) -> HTMLResponse:
    return HTMLResponse(content=RETIRED_PAGE, status_code=410)
