from __future__ import annotations

import asyncio
import base64
import hmac
import os
import time
from datetime import datetime, timezone
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

app = FastAPI(title="Telegram Bot Control Center", version="0.1.0")
security = HTTPBasic(auto_error=False)

BOTS = (
    {"id": "z28", "name": "Z28 Attendance Bot", "runtime": "Node.js",
     "health_url_env": "Z28_HEALTH_URL",
     "default_url": "https://z28-telegram-attendance-bot-pcs8.onrender.com/api/healthz",
     "service_url": "https://z28-telegram-attendance-bot-pcs8.onrender.com",
     "repo_url": "https://github.com/zhengduo1539-code/Z28-Telegram-Attendance-Bot",
     "management_url_env": "Z28_CONTROL_CENTER_URL",
     "management_url": "https://z28-telegram-attendance-bot-pcs8.onrender.com/api/control-center"},
    {"id": "admin-bot", "name": "Admin Bot", "runtime": "Python",
     "health_url_env": "ADMIN_BOT_HEALTH_URL",
     "default_url": "https://admin-bot-uvy8.onrender.com/healthz",
     "service_url": "https://admin-bot-uvy8.onrender.com",
     "repo_url": "https://github.com/zhengduo1539-code/admin_bot",
     "management_url_env": "ADMIN_BOT_CONTROL_CENTER_URL",
     "management_url": "https://admin-bot-uvy8.onrender.com/control-center"},
)


def require_dashboard_login(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> str:
    expected_user = os.getenv("DASHBOARD_USERNAME", "owner")
    expected_password = os.getenv("DASHBOARD_PASSWORD", "")
    if not expected_password:
        raise HTTPException(status_code=503, detail="Dashboard authentication is not configured.")
    provided_user = credentials.username if credentials else ""
    provided_password = credentials.password if credentials else ""
    user_ok = hmac.compare_digest(provided_user.encode(), expected_user.encode())
    password_ok = hmac.compare_digest(provided_password.encode(), expected_password.encode())
    if not (user_ok and password_ok):
        raise HTTPException(status_code=401, detail="Invalid dashboard credentials.",
                            headers={"WWW-Authenticate": "Basic"})
    return provided_user


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def check_bot(client: httpx.AsyncClient, bot: dict[str, str]) -> dict[str, Any]:
    url = os.getenv(bot["health_url_env"], bot["default_url"]).strip()
    timeout = max(1.0, min(float(os.getenv("HEALTH_CHECK_TIMEOUT", "8")), 20.0))
    started = time.perf_counter()
    result: dict[str, Any] = {
        "id": bot["id"], "name": bot["name"], "runtime": bot["runtime"],
        "service_url": bot["service_url"], "repo_url": bot["repo_url"],
        "health_url": url, "status": "unknown", "http_status": None,
        "response_time_ms": None, "checked_at": datetime.now(timezone.utc).isoformat(),
        "message": "Health check has not completed.",
    }
    try:
        response = await client.get(url, timeout=timeout, follow_redirects=True)
        result["http_status"] = response.status_code
        result["response_time_ms"] = round((time.perf_counter() - started) * 1000)
        if 200 <= response.status_code < 300:
            result["status"] = "online"
            result["message"] = "Health endpoint returned a successful response."
        else:
            result["status"] = "degraded"
            result["message"] = f"Health endpoint returned HTTP {response.status_code}."
    except httpx.TimeoutException:
        result["status"] = "unavailable"
        result["response_time_ms"] = round((time.perf_counter() - started) * 1000)
        result["message"] = f"Health check timed out after {timeout:g} seconds."
    except httpx.HTTPError:
        result["status"] = "unavailable"
        result["response_time_ms"] = round((time.perf_counter() - started) * 1000)
        result["message"] = "Could not connect to the health endpoint."
    except (ValueError, OSError):
        result["status"] = "unknown"
        result["message"] = "Health endpoint configuration is invalid."
    return result


@app.get("/api/status")
async def api_status(_: Annotated[str, Depends(require_dashboard_login)]) -> dict[str, Any]:
    limits = httpx.Limits(max_connections=6, max_keepalive_connections=3)
    async with httpx.AsyncClient(limits=limits,
            headers={"User-Agent": "Telegram-Bot-Control-Center/0.1"}) as client:
        bots = await asyncio.gather(*(check_bot(client, bot) for bot in BOTS))
    counts = {key: sum(1 for bot in bots if bot["status"] == key)
              for key in ("online", "degraded", "unavailable", "unknown")}
    return {"checked_at": datetime.now(timezone.utc).isoformat(),
            "poll_interval_seconds": 60, "summary": counts, "bots": bots}


def get_bot(bot_id: str) -> dict[str, str]:
    for bot in BOTS:
        if bot["id"] == bot_id:
            return bot
    raise HTTPException(status_code=404, detail="Unknown monitored service.")

@app.get("/api/viewer/github/{bot_id}")
async def github_viewer(bot_id: str,
    _: Annotated[str, Depends(require_dashboard_login)], path: str = "") -> dict[str, Any]:
    bot = get_bot(bot_id)
    prefix = "https://github.com/"
    if not bot["repo_url"].startswith(prefix):
        raise HTTPException(status_code=400, detail="Repository URL is not allowed.")
    parts = bot["repo_url"][len(prefix):].strip("/").split("/")    if len(parts) != 2 or any(part in ("", ".", "..") for part in parts):
        raise HTTPException(status_code=400, detail="Repository configuration is invalid.")
    owner, repo = parts
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents"
    if path.strip("/"):
        api_url += "/" + quote(path.strip("/"), safe="/")
    try:
        async with httpx.AsyncClient(timeout=12.0, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Telegram-Bot-Control-Center/0.1"}) as client:
            response = await client.get(api_url)
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Path not found or repository is not public.")
        if response.status_code == 403:
            raise HTTPException(status_code=502, detail="GitHub API rate limit or access restriction.")
        response.raise_for_status()
        data = response.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=502, detail="Could not load repository contents from GitHub.")
    if isinstance(data, list):
        entries = [{"name": x.get("name", ""), "path": x.get("path", ""),
                    "type": x.get("type", "file"), "size": x.get("size", 0)}
                   for x in data if x.get("type") in ("dir", "file")]
        entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
        return {"kind": "directory", "repository": f"{owner}/{repo}", "path": path, "entries": entries}
    if isinstance(data, dict) and data.get("type") == "file":
        content = data.get("content", "")
        decoded = base64.b64decode(content).decode("utf-8", errors="replace") if data.get("encoding") == "base64" else content
        return {"kind": "file", "repository": f"{owner}/{repo}", "path": data.get("path", path),
                "content": decoded[:200000], "truncated": len(decoded) > 200000}
    raise HTTPException(status_code=415, detail="This item is not a text file or directory.")


@app.get("/api/viewer/health/{bot_id}")
async def health_viewer(bot_id: str,
    _: Annotated[str, Depends(require_dashboard_login)]) -> dict[str, Any]:
    bot = get_bot(bot_id)
    url = os.getenv(bot["health_url_env"], bot["default_url"]).strip()
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Telegram-Bot-Control-Center/0.1"})
        return {"url": url, "http_status": response.status_code,
                "content_type": response.headers.get("content-type", "unknown"),
                "body": response.text[:100000], "truncated": len(response.text) > 100000}
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not fetch this health endpoint.")



async def proxy_management(bot_id: str, method: str = "GET", endpoint: str = "features", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    bot = get_bot(bot_id)
    base_url = os.getenv(bot["management_url_env"], bot["management_url"]).strip().rstrip("/")
    token = os.getenv("CONTROL_CENTER_API_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="Control Center API token is not configured on this service.")
    allowed = {"features": {"GET"}, "activity-limits": {"PUT"}, "activity-reminders": {"PUT"}}
    if endpoint not in allowed or method not in allowed[endpoint]:
        raise HTTPException(status_code=404, detail="Unsupported management operation.")
    if bot_id == "admin-bot" and endpoint != "features":
        raise HTTPException(status_code=400, detail="This bot has no writable calculator settings.")
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.request(method, base_url + "/" + endpoint,
                headers={"x-control-center-token": token, "Cache-Control": "no-store"}, json=payload)
        try:
            data = response.json()
        except ValueError:
            data = {"error": "Bot API returned a non-JSON response."}
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not reach this bot's feature API.")
    if response.status_code >= 400:
        detail = data.get("detail") or data.get("error") or f"Bot feature API returned HTTP {response.status_code}."
        code = response.status_code if response.status_code in (400, 401, 403, 404, 503) else 502
        raise HTTPException(status_code=code, detail=detail)
    return data


@app.get("/api/manage/{bot_id}/features")
async def management_features(bot_id: str, _: Annotated[str, Depends(require_dashboard_login)]) -> dict[str, Any]:
    return await proxy_management(bot_id)


@app.put("/api/manage/{bot_id}/activity-limits")
async def management_activity_limits(bot_id: str, payload: dict[str, Any],
    _: Annotated[str, Depends(require_dashboard_login)]) -> dict[str, Any]:
    return await proxy_management(bot_id, "PUT", "activity-limits", payload)


@app.put("/api/manage/{bot_id}/activity-reminders")
async def management_activity_reminders(bot_id: str, payload: dict[str, Any],
    _: Annotated[str, Depends(require_dashboard_login)]) -> dict[str, Any]:
    return await proxy_management(bot_id, "PUT", "activity-reminders", payload)


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0b0d12"><title>Bot Control Center</title>
<style>
:root{color-scheme:dark;--bg:#0b0d12;--panel:#121620;--panel2:#171c28;--line:#272e3d;--text:#f4f6fb;--muted:#8f9aaf;--accent:#ff963d;--green:#39d6a0;--red:#ff657a;--yellow:#f6c45b}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 80% -10%,#2a1b19 0,transparent 38%),var(--bg);color:var(--text);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
button,input{font:inherit}.wrap{max-width:1180px;margin:auto;padding:32px 22px 50px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:34px}.brand{display:flex;align-items:center;gap:13px}.logo{width:45px;height:45px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(140deg,#ffb15b,#ed6527);color:#1b1008;font-weight:900;font-size:21px;box-shadow:0 8px 28px #ff812326}.eyebrow{color:var(--accent);font-size:11px;font-weight:800;letter-spacing:.17em;text-transform:uppercase}.brand h1{font-size:20px;margin:2px 0 0;letter-spacing:-.03em}.topright{display:flex;gap:10px;align-items:center}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 13px;color:var(--text);background:var(--panel2);cursor:pointer}.btn:hover{border-color:#596176}.muted{color:var(--muted)}.hero{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:22px}.hero h2{font-size:clamp(25px,4vw,36px);letter-spacing:-.045em;line-height:1.12;margin:8px 0}.hero p{margin:8px 0 0;color:var(--muted)}.live{font-size:12px;color:var(--green);display:flex;align-items:center;gap:8px;white-space:nowrap}.dot{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 12px currentColor}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin-bottom:22px}.stat,.card{background:linear-gradient(145deg,#151a25,#10141c);border:1px solid var(--line);border-radius:16px}.stat{padding:17px 18px}.statlabel{color:var(--muted);font-size:12px}.statnum{font-size:29px;font-weight:750;letter-spacing:-.04em;margin-top:6px}.statnum.green{color:var(--green)}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.card{padding:19px;min-width:0}.cardhead{display:flex;justify-content:space-between;align-items:start;gap:10px}.botidentity{display:flex;align-items:center;gap:12px;min-width:0}.boticon{width:42px;height:42px;flex-shrink:0;border-radius:13px;display:grid;place-items:center;background:#24202a;border:1px solid #3c2c2a;color:var(--accent);font-weight:850}.botname{font-size:16px;font-weight:750;letter-spacing:-.02em}.runtime{font-size:12px;color:var(--muted);margin-top:2px}.pill{font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:800;padding:5px 8px;border-radius:7px;background:#242b37;color:var(--muted);white-space:nowrap}.pill.online{color:var(--green);background:#123128}.pill.degraded{color:var(--yellow);background:#382d18}.pill.unavailable{color:var(--red);background:#3a2029}.details{display:flex;justify-content:space-between;align-items:center;border-top:1px solid var(--line);margin-top:19px;padding-top:14px;font-size:12px}.latency{font-size:13px;font-weight:700}.message{font-size:12px;color:var(--muted);min-height:36px;margin:13px 0 17px}.links{display:flex;gap:9px;flex-wrap:wrap}.links a{color:#ffc18c;text-decoration:none;border:1px solid #4a3024;border-radius:8px;padding:7px 9px;font-size:12px}.links a:hover{background:#291d17}.foot{display:flex;justify-content:space-between;gap:12px;margin-top:20px;font-size:12px;color:var(--muted)}.error{color:var(--red);font-size:13px;margin-top:14px}.hidden{display:none!important}.overview-banner{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:15px 17px;margin:0 0 20px;border:1px solid var(--line);border-radius:13px;background:linear-gradient(100deg,#171c25,#12161e)}.overview-main{display:flex;align-items:center;gap:10px;min-width:0}.overview-banner .dot{flex-shrink:0}.overview-title{font-size:13px;font-weight:800}.overview-message{font-size:12px;color:var(--muted);margin-left:5px}.toolbar{display:flex;align-items:end;gap:12px;margin:0 0 14px;flex-wrap:wrap}.toolbar-field{display:grid;gap:5px}.toolbar-field label{font-size:11px;color:var(--muted);font-weight:750;text-transform:uppercase;letter-spacing:.08em}.toolbar input,.toolbar select{min-width:190px;background:#10141c;border:1px solid var(--line);border-radius:9px;padding:10px 12px;color:var(--text);outline:none}.toolbar input:focus,.toolbar select:focus{border-color:var(--accent)}.service-count{margin-left:auto;color:var(--muted);font-size:12px;padding-bottom:10px}.endpoint{display:block;overflow-wrap:anywhere;font-size:11px;color:#9da8bb;margin-top:8px}.card .details+.details{margin-top:0;padding-top:9px;border-top:0}.card .links a.health-link{color:#f3f5fa;border-color:var(--line)}.hidden{display:none!important}.viewer-backdrop{position:fixed;inset:0;z-index:1000;background:#05070bd9;backdrop-filter:blur(8px);display:grid;place-items:center;padding:18px}.viewer-panel{width:min(1120px,100%);height:min(82vh,850px);display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden;box-shadow:0 24px 90px #0009}.viewer-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 16px;border-bottom:1px solid var(--line)}.viewer-title{font-weight:800;min-width:0;overflow-wrap:anywhere}.viewer-actions{display:flex;gap:8px;align-items:center}.viewer-content{flex:1;min-height:0;overflow:auto;background:#0d1119;color:#e9edf5;padding:18px}.viewer-content button{color:#ffbd86;background:#171e2a;border:1px solid #30394a;border-radius:8px;padding:8px 10px;cursor:pointer;text-align:left}.viewer-content button:hover{border-color:var(--accent)}.viewer-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}.viewer-breadcrumb{font-size:12px;color:#aab4c6;overflow-wrap:anywhere}.viewer-list{display:grid;gap:7px}.viewer-entry{display:flex;justify-content:space-between;align-items:center;gap:12px;width:100%}.viewer-entry small{color:#8f9aaf;white-space:nowrap}.viewer-code{white-space:pre-wrap;overflow-wrap:anywhere;background:#080b11;border:1px solid #252d3b;border-radius:10px;padding:15px;font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;color:#e5eaf3}.viewer-message{padding:14px;border:1px solid #30394a;border-radius:10px;color:#b8c2d4}.viewer-note{padding:9px 14px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}@media(max-width:560px){.viewer-backdrop{padding:7px}.viewer-panel{height:90vh;border-radius:12px}.viewer-head{padding:10px}.viewer-title{font-size:13px}}.activity{margin-top:18px;padding:20px}.activity-head{display:flex;justify-content:space-between;align-items:start;gap:14px;margin-bottom:14px}.activity-head h3{margin:5px 0;font-size:17px}.activity-head p{font-size:12px;margin:4px 0}.activity-list{display:grid;gap:8px}.activity-row{display:flex;justify-content:space-between;gap:14px;align-items:center;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:#0e121a}.activity-copy{min-width:0;font-size:12px}.activity-time{color:var(--muted);font-size:11px;white-space:nowrap}.trend{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:10px 0 2px}.trend-label{font-size:11px;color:var(--muted)}.trend svg{width:112px;height:26px;overflow:visible}.trend-empty{font-size:11px;color:var(--muted)}.login{max-width:410px;margin:9vh auto;padding:28px}.login h2{margin:0 0 7px}.field{display:block;margin:15px 0 5px;color:var(--muted);font-size:13px}.login input{width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;background:#0c1017;color:var(--text);outline:none}.login input:focus{border-color:var(--accent)}.primary{width:100%;margin-top:20px;background:var(--accent);border:0;border-radius:10px;padding:12px;color:#211208;font-weight:850;cursor:pointer}.login small{display:block;color:var(--muted);margin-top:15px}.skeleton{height:210px;border-radius:16px;background:linear-gradient(100deg,#141923 20%,#202633 38%,#141923 60%);background-size:200% 100%;animation:shimmer 1.4s infinite}@keyframes shimmer{to{background-position:-200% 0}}@media(max-width:850px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.wrap{padding:22px 14px 35px}.top{align-items:start}.brand h1{font-size:17px}.topright .btn{padding:8px}.hero{align-items:start;flex-direction:column}.cards{grid-template-columns:1fr}.stat{padding:14px}.statnum{font-size:25px}.foot{flex-direction:column}}
</style></head><body>
<div id="login" class="wrap hidden"><div class="card login">
<div class="brand"><div class="logo">BC</div><div><div class="eyebrow">Private access</div><h1>Bot Control Center</h1></div></div>
<h2 style="margin-top:28px">Welcome back.</h2><p class="muted">Sign in to monitor your Telegram bot services.</p>
<form id="loginForm"><label class="field" for="username">Username</label><input id="username" autocomplete="username" value="owner" required>
<label class="field" for="password">Password</label><input id="password" type="password" autocomplete="current-password" required>
<button class="primary" type="submit">Sign in securely</button><div id="loginError" class="error"></div></form><small>Private monitoring · Credentials stay in this browser session.</small></div></div>
<div id="app" class="wrap hidden"><header class="top">
<div class="brand"><div class="logo">BC</div><div><div class="eyebrow">Operations · Private</div><h1>Bot Control Center</h1></div></div>
<div class="topright"><span id="liveLabel" class="live"><span class="dot"></span> Monitoring</span><button id="refresh" class="btn">↻ Refresh</button><button id="autoRefresh" class="btn" aria-pressed="true">Auto-refresh: On</button><button id="exportReport" class="btn">Export CSV</button><button id="logout" class="btn">Sign out</button></div></header>
<section class="hero"><div><div class="eyebrow">Service overview</div><h2>Your bots. One view.</h2><p>Live health checks for your Telegram bot infrastructure.</p></div><div class="muted" id="checkedAt">Waiting for first check…</div></section>
<section class="overview-banner" aria-live="polite"><div class="overview-main"><span id="overallDot" class="dot" style="color:var(--muted)"></span><div><span id="overallTitle" class="overview-title">Checking system health</span><span id="overallMessage" class="overview-message">Waiting for the first health check.</span></div></div><span id="overallCount" class="muted" style="font-size:12px;white-space:nowrap">3 services configured</span></section>
<section class="stats"><div class="stat"><div class="statlabel">Monitored services</div><div class="statnum" id="total">2</div></div><div class="stat"><div class="statlabel">Online</div><div class="statnum green" id="online">—</div></div><div class="stat"><div class="statlabel">Needs attention</div><div class="statnum" id="attention">—</div></div><div class="stat"><div class="statlabel">Unavailable</div><div class="statnum" id="unavailable">—</div></div></section>
<section class="toolbar" aria-label="Service filters"><div class="toolbar-field"><label for="serviceSearch">Search services</label><input id="serviceSearch" type="search" placeholder="Search by name or runtime…" autocomplete="off"></div><div class="toolbar-field"><label for="statusFilter">Status</label><select id="statusFilter"><option value="all">All statuses</option><option value="online">Online</option><option value="degraded">Degraded</option><option value="unavailable">Unavailable</option><option value="unknown">Unknown</option></select></div><span id="serviceCount" class="service-count">2 services</span></section>
<section id="cards" class="cards"><div class="skeleton"></div><div class="skeleton"></div></section><div id="emptyState" class="muted hidden" style="padding:30px;text-align:center">No services match these filters.</div><section class="activity card"><div class="activity-head"><div><div class="eyebrow">Local history</div><h3>Recent status changes</h3><p class="muted">Recorded in this browser only; history starts after this feature is enabled.</p></div><button id="clearHistory" class="btn">Clear history</button></div><div id="activityList" class="activity-list"><div class="viewer-message">Waiting for health checks…</div></div></section><div id="pageError" class="error" role="alert"></div><footer class="foot"><span>Control Center v0.2 · Monitoring and bot feature management</span><span>Auto-refresh every 60 seconds</span></footer></div>
<div id="viewer" class="viewer-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="viewerTitle"><section class="viewer-panel"><header class="viewer-head"><div id="viewerTitle" class="viewer-title">Service viewer</div><div class="viewer-actions"><button id="viewerClose" class="btn" type="button" aria-label="Close viewer">✕ Close</button></div></header><div id="viewerContent" class="viewer-content" tabindex="0"><div class="viewer-message">Choose a dashboard item to inspect it here.</div></div><div class="viewer-note">Internal viewer · Feature changes are sent to protected bot APIs; no infrastructure controls are exposed.</div></section></div>
<script>
const $=id=>document.getElementById(id);let auth=sessionStorage.getItem('bcc_auth')||'',latestBots=[],autoRefresh=true,refreshing=false;const HISTORY_KEY='bcc_local_history_v1',MAX_SAMPLES=1440,MAX_EVENTS=60;
function showLogin(){$('app').classList.add('hidden');$('login').classList.remove('hidden')}
function showApp(){$('login').classList.add('hidden');$('app').classList.remove('hidden')}
function escapeHtml(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function statusLabel(s){return({online:'Online',degraded:'Degraded',unavailable:'Unavailable',unknown:'Unknown'})[s]||'Unknown'}
function card(b){const symbol=b.id==='z28'?'Z':'A';const latency=b.response_time_ms==null?'—':b.response_time_ms+' ms';const http=b.http_status==null?'No response':'HTTP '+b.http_status;return '<article class="card"><div class="cardhead"><div class="botidentity"><div class="boticon">'+symbol+'</div><div><div class="botname">'+escapeHtml(b.name)+'</div><div class="runtime">'+escapeHtml(b.runtime)+' · Render</div></div></div><span class="pill '+escapeHtml(b.status)+'">'+statusLabel(b.status)+'</span></div><div class="details"><span class="muted">Response time</span><span class="latency">'+latency+'</span></div><div class="details"><span class="muted">HTTP response</span><span class="latency">'+http+'</span></div><span class="endpoint">Health check: '+escapeHtml(b.health_url)+'</span><p class="message">'+escapeHtml(b.message)+'</p><div class="links"><a href="'+escapeHtml(b.service_url)+'" data-viewer-kind="service" data-bot-id="'+escapeHtml(b.id)+'" data-viewer-title="'+escapeHtml(b.name)+' · Service">Open service</a><a class="health-link" href="'+escapeHtml(b.health_url)+'" data-viewer-kind="health" data-bot-id="'+escapeHtml(b.id)+'" data-viewer-title="'+escapeHtml(b.name)+' · Health endpoint">Health endpoint</a><a href="#" data-viewer-kind="manage" data-bot-id="'+escapeHtml(b.id)+'" data-viewer-title="'+escapeHtml(b.name)+' · Feature management">Manage features</a><a href="'+escapeHtml(b.repo_url)+'" data-viewer-kind="github" data-bot-id="'+escapeHtml(b.id)+'" data-viewer-title="'+escapeHtml(b.name)+' · GitHub repository">GitHub repo</a></div></article>';}
function readHistory(){try{const raw=localStorage.getItem(HISTORY_KEY);if(!raw)return {samples:{},events:[]};const parsed=JSON.parse(raw);return {samples:parsed.samples&&typeof parsed.samples==='object'?parsed.samples:{},events:Array.isArray(parsed.events)?parsed.events:[]};}catch(_){return {samples:{},events:[]};}}
function writeHistory(h){try{localStorage.setItem(HISTORY_KEY,JSON.stringify(h));}catch(_){/* Local history is optional; dashboard monitoring must keep working. */}}
function recordHistory(bots,checkedAt){const h=readHistory(),stamp=Date.parse(checkedAt)||Date.now();for(const b of bots){const list=Array.isArray(h.samples[b.id])?h.samples[b.id]:[];const previous=list.length?list[list.length-1]:null;list.push({t:stamp,s:b.status,ms:Number.isFinite(b.response_time_ms)?b.response_time_ms:null});h.samples[b.id]=list.slice(-MAX_SAMPLES);if(previous&&previous.s!==b.status){h.events.unshift({t:stamp,id:b.id,name:b.name,from:previous.s,to:b.status});}}h.events=h.events.slice(0,MAX_EVENTS);writeHistory(h);}
function sparkline(id){const h=readHistory(),samples=(h.samples[id]||[]).slice(-30),vals=samples.map(x=>x.ms).filter(x=>Number.isFinite(x));if(vals.length<2)return '<span class="trend-empty">Collecting trend…</span>';const min=Math.min(...vals),max=Math.max(...vals),range=Math.max(1,max-min);const points=vals.map((v,i)=>(i/(vals.length-1)*100).toFixed(1)+','+(22-(v-min)/range*18).toFixed(1)).join(' ');return '<svg viewBox="0 0 100 26" role="img" aria-label="Recent response time trend"><polyline points="'+points+'" fill="none" stroke="#ff963d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';}
function observedAvailability(id){const h=readHistory(),samples=h.samples[id]||[];if(!samples.length)return '—';const good=samples.filter(x=>x.s==='online').length;return (good/samples.length*100).toFixed(1)+'%';}
function renderActivity(){const events=readHistory().events;$('activityList').innerHTML=events.length?events.slice(0,12).map(x=>'<div class="activity-row"><div class="activity-copy"><strong>'+escapeHtml(x.name)+'</strong> · '+escapeHtml(statusLabel(x.from))+' → <strong>'+escapeHtml(statusLabel(x.to))+'</strong></div><time class="activity-time">'+escapeHtml(new Date(x.t).toLocaleString())+'</time></div>').join(''):'<div class="viewer-message">No status changes recorded yet. Changes will appear here after a service changes status between checks.</div>';}
function renderCards(){const query=$('serviceSearch').value.trim().toLowerCase();const status=$('statusFilter').value;const filtered=latestBots.filter(b=>(status==='all'||b.status===status)&&(!query||(b.name+' '+b.runtime+' '+b.id).toLowerCase().includes(query)));$('cards').innerHTML=filtered.map(b=>card(b).replace('</p><div class="links">','</p><div class="trend"><span class="trend-label">Recent latency</span>'+sparkline(b.id)+'</div><div class="details"><span class="muted">Observed availability</span><span class="latency">'+observedAvailability(b.id)+'</span></div><div class="links">')).join('');$('emptyState').classList.toggle('hidden',filtered.length>0);$('serviceCount').textContent=filtered.length+' of '+latestBots.length+' services';renderActivity();}
async function loadStatus(){if(!auth){showLogin();return}if(refreshing)return;refreshing=true;$('refresh').disabled=true;$('pageError').textContent='';$('liveLabel').innerHTML='<span class="dot"></span> Checking';try{const r=await fetch('/api/status',{headers:{Authorization:'Basic '+auth},cache:'no-store'});if(r.status===401){auth='';sessionStorage.removeItem('bcc_auth');$('loginError').textContent='Invalid username or password.';showLogin();return}if(r.status===503)throw new Error('Dashboard password is not configured on the server. Set DASHBOARD_PASSWORD in Render environment variables.');if(!r.ok)throw new Error('Health check request failed (HTTP '+r.status+').');const d=await r.json();recordHistory(d.bots,d.checked_at);$('total').textContent=d.bots.length;$('online').textContent=d.summary.online;$('attention').textContent=d.summary.degraded+d.summary.unknown;$('unavailable').textContent=d.summary.unavailable;latestBots=d.bots;renderCards();const total=d.bots.length,online=d.summary.online,down=d.summary.unavailable,needs=d.summary.degraded+d.summary.unknown;$('overallCount').textContent=online+' / '+total+' services online';if(online===total){$('overallDot').style.color='var(--green)';$('overallTitle').textContent='All systems operational';$('overallMessage').textContent='All monitored health endpoints are responding.'}else if(down>0){$('overallDot').style.color='var(--red)';$('overallTitle').textContent='Service outage detected';$('overallMessage').textContent=down+' service(s) unavailable; review the affected card(s).'}else{$('overallDot').style.color='var(--yellow)';$('overallTitle').textContent='Attention required';$('overallMessage').textContent=needs+' service(s) need review.'}$('checkedAt').textContent='Last checked '+new Date(d.checked_at).toLocaleTimeString();$('liveLabel').innerHTML='<span class="dot"></span> Monitoring';showApp()}catch(e){$('pageError').textContent=e.message||'Could not load status.';$('liveLabel').textContent='Connection issue';if(!auth)showLogin()}finally{refreshing=false;$('refresh').disabled=false}}
$('loginForm').addEventListener('submit',async e=>{e.preventDefault();$('loginError').textContent='';auth=btoa(unescape(encodeURIComponent($('username').value))+':'+unescape(encodeURIComponent($('password').value)));sessionStorage.setItem('bcc_auth',auth);await loadStatus();if(!$('app').classList.contains('hidden'))$('password').value=''});
let viewerBotId='';
function closeViewer(){$('viewer').classList.add('hidden');$('viewerContent').innerHTML='';document.body.style.overflow='';}
function viewerError(m){$('viewerContent').innerHTML='<div class="viewer-message">'+escapeHtml(m)+'</div>';}
async function loadGithubPath(path=''){ $('viewerContent').innerHTML='<div class="viewer-message">Loading repository…</div>';try{const r=await fetch('/api/viewer/github/'+encodeURIComponent(viewerBotId)+'?path='+encodeURIComponent(path),{headers:{Authorization:'Basic '+auth},cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.detail||'Could not load repository.');if(d.kind==='directory'){const parent=path.includes('/')?path.slice(0,path.lastIndexOf('/')):'';let h='<div class="viewer-toolbar">'+(path?'<button data-github-path="'+escapeHtml(parent)+'">← Back</button>':'')+'<span class="viewer-breadcrumb">'+escapeHtml(d.repository)+' / '+escapeHtml(path)+'</span></div><div class="viewer-list">';for(const x of d.entries)h+='<button class="viewer-entry" data-github-path="'+escapeHtml(x.path)+'"><span>'+(x.type==='dir'?'📁 ':'📄 ')+escapeHtml(x.name)+'</span><small>'+(x.type==='dir'?'Folder':Number(x.size||0).toLocaleString()+' bytes')+'</small></button>';h+='</div>';$('viewerContent').innerHTML=h;}else $('viewerContent').innerHTML='<div class="viewer-toolbar"><button data-github-path="'+escapeHtml(path.includes('/')?path.slice(0,path.lastIndexOf('/')):'')+'">← Back to files</button><span class="viewer-breadcrumb">'+escapeHtml(d.path)+'</span></div><pre class="viewer-code">'+escapeHtml(d.content)+(d.truncated?'\n[Preview truncated at 200 KB.]':'')+'</pre>';}catch(e){viewerError(e.message||'Repository request failed.');}}

async function loadManagement(id){
  $('viewerContent').innerHTML='<div class="viewer-message">Loading live bot settings…</div>';
  try{
    const r=await fetch('/api/manage/'+encodeURIComponent(id)+'/features',{headers:{Authorization:'Basic '+auth},cache:'no-store'});
    const d=await r.json();if(!r.ok)throw Error(d.detail||'Feature settings are not available.');
    let h='<div class="viewer-message">Settings are read from the bot backend. Changes are saved to bot storage, not this browser.</div>';
    if(d.activityLimits){
      h+='<section class="card" style="margin-top:14px"><h3 style="margin:0 0 8px">Activity time limits</h3><p class="muted" style="font-size:12px">Set the duration in minutes (1–1440). Saved values apply to the existing attendance logic.</p><div class="viewer-list">';
      for(const kind of ['eat','wc','smoke','wcd'])h+='<div class="activity-row"><label for="limit-'+kind+'">'+kind.toUpperCase()+'</label><input id="limit-'+kind+'" type="number" min="1" max="1440" step="1" value="'+escapeHtml(d.activityLimits[kind]??'')+'" style="width:110px;background:#0c1017;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px"><button data-save-limit="'+kind+'" data-bot-id="'+escapeHtml(id)+'">Save</button></div>';
      h+='</div></section>';
    }
    if(typeof d.remindersEnabled==='boolean')h+='<section class="card" style="margin-top:14px"><h3 style="margin:0 0 8px">Activity reminders</h3><p class="muted" style="font-size:12px">Current state: '+(d.remindersEnabled?'Enabled':'Disabled')+'</p><button data-reminders="'+(!d.remindersEnabled)+'" data-bot-id="'+escapeHtml(id)+'">'+(d.remindersEnabled?'Disable reminders':'Enable reminders')+'</button></section>';
    if(d.statistics){h+='<section class="card" style="margin-top:14px"><h3 style="margin:0 0 8px">Calculator bot statistics</h3><pre class="viewer-code">'+escapeHtml(JSON.stringify(d.statistics,null,2))+'</pre><p class="muted" style="font-size:12px">Read-only: this bot currently has no persistent calculator settings to edit.</p><p class="muted" style="font-size:12px">Database: '+escapeHtml(d.mongodb||'unknown')+'</p></section>';}
    if(d.management_note)h+='<p class="muted" style="margin-top:12px">'+escapeHtml(d.management_note)+'</p>';
    $('viewerContent').innerHTML=h;
  }catch(e){viewerError((e.message||'Could not load bot features.')+' Check that the bot’s protected Control Center API and shared token are configured.');}
}
async function saveManagement(path,payload,button){
  button.disabled=true;const old=button.textContent;button.textContent='Saving…';
  try{const r=await fetch('/api/manage/'+encodeURIComponent(viewerBotId)+'/'+path,{method:'PUT',headers:{Authorization:'Basic '+auth,'Content-Type':'application/json'},body:JSON.stringify(payload),cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.detail||'Save failed.');await loadManagement(viewerBotId);}
  catch(e){viewerError(e.message||'Could not save the setting.');button.disabled=false;button.textContent=old;}
}
document.addEventListener('click',e=>{
  const limit=e.target.closest('[data-save-limit]');
  if(limit){const kind=limit.dataset.saveLimit,input=$('limit-'+kind),minutes=Number(input?.value);if(!Number.isSafeInteger(minutes)||minutes<1||minutes>1440){viewerError('Enter a whole number between 1 and 1440 minutes.');return;}saveManagement('activity-limits',{kind,minutes},limit);return;}
  const reminders=e.target.closest('[data-reminders]');
  if(reminders)saveManagement('activity-reminders',{enabled:reminders.dataset.reminders==='true'},reminders);
});
async function openViewer(kind,id,title){viewerBotId=id;$('viewerTitle').textContent=title;$('viewer').classList.remove('hidden');document.body.style.overflow='hidden';$('viewerContent').innerHTML='<div class="viewer-message">Loading…</div>';if(kind==='github'){await loadGithubPath('');return;}if(kind==='manage'){await loadManagement(id);return;}const b=latestBots.find(x=>x.id===id);if(!b){viewerError('Refresh the dashboard and try again.');return;}if(kind==='service'){$('viewerContent').innerHTML='<div class="viewer-toolbar"><span class="pill '+escapeHtml(b.status)+'">'+statusLabel(b.status)+'</span><span class="viewer-breadcrumb">'+escapeHtml(b.service_url)+'</span></div><div class="viewer-message">External service pages can block embedding. This internal view displays the service monitoring information instead.<br><br>HTTP: '+escapeHtml(b.http_status??'No response')+'<br>Response time: '+escapeHtml(b.response_time_ms==null?'—':b.response_time_ms+' ms')+'<br>Last check: '+escapeHtml(new Date(b.checked_at).toLocaleString())+'<br>'+escapeHtml(b.message)+'</div>';return;}try{const r=await fetch('/api/viewer/health/'+encodeURIComponent(id),{headers:{Authorization:'Basic '+auth},cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.detail||'Could not fetch endpoint.');$('viewerContent').innerHTML='<div class="viewer-toolbar"><span class="pill '+(d.http_status>=200&&d.http_status<300?'online':'unavailable')+'">HTTP '+escapeHtml(d.http_status)+'</span><span class="viewer-breadcrumb">'+escapeHtml(d.url)+' · '+escapeHtml(d.content_type)+'</span></div><pre class="viewer-code">'+escapeHtml(d.body)+(d.truncated?'\n[Response truncated at 100 KB.]':'')+'</pre>';}catch(e){viewerError(e.message||'Health endpoint request failed.');}}
document.addEventListener('click',e=>{const f=e.target.closest('[data-github-path]');if(f&&!$('viewer').classList.contains('hidden')){loadGithubPath(f.dataset.githubPath||'');return;}const a=e.target.closest('#cards .links a');if(!a)return;e.preventDefault();openViewer(a.dataset.viewerKind,a.dataset.botId,a.dataset.viewerTitle||a.textContent.trim());});
$('viewerClose').addEventListener('click',closeViewer);$('viewer').addEventListener('click',e=>{if(e.target===$('viewer'))closeViewer()});document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('viewer').classList.contains('hidden'))closeViewer()});
$('serviceSearch').addEventListener('input',renderCards);$('statusFilter').addEventListener('change',renderCards);$('refresh').addEventListener('click',loadStatus);$('autoRefresh').addEventListener('click',()=>{autoRefresh=!autoRefresh;$('autoRefresh').textContent='Auto-refresh: '+(autoRefresh?'On':'Off');$('autoRefresh').setAttribute('aria-pressed',String(autoRefresh));$('liveLabel').innerHTML='<span class="dot"></span> '+(autoRefresh?'Monitoring':'Paused');});$('clearHistory').addEventListener('click',()=>{if(confirm('Clear locally stored health samples and status-change history for this browser?')){try{localStorage.removeItem(HISTORY_KEY)}catch(_){}renderCards();}});$('exportReport').addEventListener('click',()=>{const rows=[['service_id','service_name','status','http_status','response_time_ms','checked_at','health_url'],...latestBots.map(b=>[b.id,b.name,b.status,b.http_status??'',b.response_time_ms??'',b.checked_at,b.health_url])];const csv=rows.map(row=>row.map(v=>{let s=String(v??'');if(/^[=+@\-]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';}).join(',')).join('\r\n');const blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='bot-control-center-report-'+new Date().toISOString().replace(/[:.]/g,'-')+'.csv';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);});$('logout').addEventListener('click',()=>{auth='';sessionStorage.removeItem('bcc_auth');$('password').value='';showLogin()});
if(auth)loadStatus();else showLogin();setInterval(()=>{if(auth&&autoRefresh)loadStatus()},60000);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML, headers={"Cache-Control": "no-store",
                                                 "X-Content-Type-Options": "nosniff"})