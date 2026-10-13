from __future__ import annotations

import asyncio
import hmac
import os
import time
from datetime import datetime, timezone
from typing import Annotated, Any

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
     "repo_url": "https://github.com/zhengduo1539-code/Z28-Telegram-Attendance-Bot"},
    {"id": "admin-bot", "name": "Admin Bot", "runtime": "Python",
     "health_url_env": "ADMIN_BOT_HEALTH_URL",
     "default_url": "https://admin-bot-uvy8.onrender.com/healthz",
     "service_url": "https://admin-bot-uvy8.onrender.com",
     "repo_url": "https://github.com/zhengduo1539-code/admin_bot"},
    {"id": "m58", "name": "M58 Attendance Bot", "runtime": "Node.js",
     "health_url_env": "M58_HEALTH_URL",
     "default_url": "https://m58-telegram-attendance-bot.onrender.com/api/healthz",
     "service_url": "https://m58-telegram-attendance-bot.onrender.com",
     "repo_url": "https://github.com/zhengduo1539-code/M58-Telegram-Attendance-Bot"},
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


@app.get("/healthz")
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


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0b0d12"><title>Bot Control Center</title>
<style>
:root{color-scheme:dark;--bg:#0b0d12;--panel:#121620;--panel2:#171c28;--line:#272e3d;--text:#f4f6fb;--muted:#8f9aaf;--accent:#ff963d;--green:#39d6a0;--red:#ff657a;--yellow:#f6c45b}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 80% -10%,#2a1b19 0,transparent 38%),var(--bg);color:var(--text);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
button,input{font:inherit}.wrap{max-width:1180px;margin:auto;padding:32px 22px 50px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:34px}.brand{display:flex;align-items:center;gap:13px}.logo{width:45px;height:45px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(140deg,#ffb15b,#ed6527);color:#1b1008;font-weight:900;font-size:21px;box-shadow:0 8px 28px #ff812326}.eyebrow{color:var(--accent);font-size:11px;font-weight:800;letter-spacing:.17em;text-transform:uppercase}.brand h1{font-size:20px;margin:2px 0 0;letter-spacing:-.03em}.topright{display:flex;gap:10px;align-items:center}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 13px;color:var(--text);background:var(--panel2);cursor:pointer}.btn:hover{border-color:#596176}.muted{color:var(--muted)}.hero{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:22px}.hero h2{font-size:clamp(25px,4vw,36px);letter-spacing:-.045em;line-height:1.12;margin:8px 0}.hero p{margin:8px 0 0;color:var(--muted)}.live{font-size:12px;color:var(--green);display:flex;align-items:center;gap:8px;white-space:nowrap}.dot{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 12px currentColor}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin-bottom:22px}.stat,.card{background:linear-gradient(145deg,#151a25,#10141c);border:1px solid var(--line);border-radius:16px}.stat{padding:17px 18px}.statlabel{color:var(--muted);font-size:12px}.statnum{font-size:29px;font-weight:750;letter-spacing:-.04em;margin-top:6px}.statnum.green{color:var(--green)}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px}.card{padding:19px;min-width:0}.cardhead{display:flex;justify-content:space-between;align-items:start;gap:10px}.botidentity{display:flex;align-items:center;gap:12px;min-width:0}.boticon{width:42px;height:42px;flex-shrink:0;border-radius:13px;display:grid;place-items:center;background:#24202a;border:1px solid #3c2c2a;color:var(--accent);font-weight:850}.botname{font-size:16px;font-weight:750;letter-spacing:-.02em}.runtime{font-size:12px;color:var(--muted);margin-top:2px}.pill{font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:800;padding:5px 8px;border-radius:7px;background:#242b37;color:var(--muted);white-space:nowrap}.pill.online{color:var(--green);background:#123128}.pill.degraded{color:var(--yellow);background:#382d18}.pill.unavailable{color:var(--red);background:#3a2029}.details{display:flex;justify-content:space-between;align-items:center;border-top:1px solid var(--line);margin-top:19px;padding-top:14px;font-size:12px}.latency{font-size:13px;font-weight:700}.message{font-size:12px;color:var(--muted);min-height:36px;margin:13px 0 17px}.links{display:flex;gap:9px;flex-wrap:wrap}.links a{color:#ffc18c;text-decoration:none;border:1px solid #4a3024;border-radius:8px;padding:7px 9px;font-size:12px}.links a:hover{background:#291d17}.foot{display:flex;justify-content:space-between;gap:12px;margin-top:20px;font-size:12px;color:var(--muted)}.error{color:var(--red);font-size:13px;margin-top:14px}.hidden{display:none!important}.login{max-width:410px;margin:9vh auto;padding:28px}.login h2{margin:0 0 7px}.field{display:block;margin:15px 0 5px;color:var(--muted);font-size:13px}.login input{width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;background:#0c1017;color:var(--text);outline:none}.login input:focus{border-color:var(--accent)}.primary{width:100%;margin-top:20px;background:var(--accent);border:0;border-radius:10px;padding:12px;color:#211208;font-weight:850;cursor:pointer}.login small{display:block;color:var(--muted);margin-top:15px}.skeleton{height:210px;border-radius:16px;background:linear-gradient(100deg,#141923 20%,#202633 38%,#141923 60%);background-size:200% 100%;animation:shimmer 1.4s infinite}@keyframes shimmer{to{background-position:-200% 0}}@media(max-width:850px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.wrap{padding:22px 14px 35px}.top{align-items:start}.brand h1{font-size:17px}.topright .btn{padding:8px}.hero{align-items:start;flex-direction:column}.cards{grid-template-columns:1fr}.stat{padding:14px}.statnum{font-size:25px}.foot{flex-direction:column}}
</style></head><body>
<div id="login" class="wrap hidden"><div class="card login">
<div class="brand"><div class="logo">BC</div><div><div class="eyebrow">Private access</div><h1>Bot Control Center</h1></div></div>
<h2 style="margin-top:28px">Welcome back.</h2><p class="muted">Sign in to monitor your Telegram bot services.</p>
<form id="loginForm"><label class="field" for="username">Username</label><input id="username" autocomplete="username" value="owner" required>
<label class="field" for="password">Password</label><input id="password" type="password" autocomplete="current-password" required>
<button class="primary" type="submit">Sign in securely</button><div id="loginError" class="error"></div></form>
<small>Private monitoring · Credentials stay in this browser session.</small></div></div>
<div id="app" class="wrap hidden"><header class="top">
<div class="brand"><div class="logo">BC</div><div><div class="eyebrow">Operations · Private</div><h1>Bot Control Center</h1></div></div>
<div class="topright"><span id="liveLabel" class="live"><span class="dot"></span> Monitoring</span><button id="refresh" class="btn">↻ Refresh</button><button id="logout" class="btn">Sign out</button></div></header>
<section class="hero"><div><div class="eyebrow">Service overview</div><h2>Your bots. One view.</h2><p>Live health checks for your Telegram bot infrastructure.</p></div><div class="muted" id="checkedAt">Waiting for first check…</div></section>
<section class="stats"><div class="stat"><div class="statlabel">Monitored services</div><div class="statnum" id="total">3</div></div><div class="stat"><div class="statlabel">Online</div><div class="statnum green" id="online">—</div></div><div class="stat"><div class="statlabel">Needs attention</div><div class="statnum" id="attention">—</div></div><div class="stat"><div class="statlabel">Unavailable</div><div class="statnum" id="unavailable">—</div></div></section>
<section id="cards" class="cards"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></section><div id="pageError" class="error"></div>
<footer class="foot"><span>Control Center v0.1 · Monitoring only — no bot settings are changed.</span><span>Auto-refresh every 60 seconds</span></footer></div>
<script>
const $=id=>document.getElementById(id);let auth=sessionStorage.getItem('bcc_auth')||'';
function showLogin(){$('app').classList.add('hidden');$('login').classList.remove('hidden')}
function showApp(){$('login').classList.add('hidden');$('app').classList.remove('hidden')}
function escapeHtml(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function statusLabel(s){return({online:'Online',degraded:'Degraded',unavailable:'Unavailable',unknown:'Unknown'})[s]||'Unknown'}
function card(b){const symbol=b.id==='z28'?'Z':b.id==='m58'?'M':'A';const latency=b.response_time_ms==null?'—':b.response_time_ms+' ms';return '<article class="card"><div class="cardhead"><div class="botidentity"><div class="boticon">'+symbol+'</div><div><div class="botname">'+escapeHtml(b.name)+'</div><div class="runtime">'+escapeHtml(b.runtime)+' · Render</div></div></div><span class="pill '+escapeHtml(b.status)+'">'+statusLabel(b.status)+'</span></div><div class="details"><span class="muted">Response time</span><span class="latency">'+latency+'</span></div><p class="message">'+escapeHtml(b.message)+'</p><div class="links"><a href="'+escapeHtml(b.service_url)+'" target="_blank" rel="noopener noreferrer">Open service ↗</a><a href="'+escapeHtml(b.repo_url)+'" target="_blank" rel="noopener noreferrer">GitHub repo ↗</a></div></article>'}
async function loadStatus(){if(!auth){showLogin();return}$('pageError').textContent='';$('liveLabel').innerHTML='<span class="dot"></span> Checking';try{const r=await fetch('/api/status',{headers:{Authorization:'Basic '+auth},cache:'no-store'});if(r.status===401){auth='';sessionStorage.removeItem('bcc_auth');$('loginError').textContent='Invalid username or password.';showLogin();return}if(r.status===503)throw new Error('Dashboard password is not configured on the server. Set DASHBOARD_PASSWORD in Render environment variables.');if(!r.ok)throw new Error('Health check request failed (HTTP '+r.status+').');const d=await r.json();$('total').textContent=d.bots.length;$('online').textContent=d.summary.online;$('attention').textContent=d.summary.degraded+d.summary.unknown;$('unavailable').textContent=d.summary.unavailable;$('cards').innerHTML=d.bots.map(card).join('');$('checkedAt').textContent='Last checked '+new Date(d.checked_at).toLocaleTimeString();$('liveLabel').innerHTML='<span class="dot"></span> Monitoring';showApp()}catch(e){$('pageError').textContent=e.message||'Could not load status.';$('liveLabel').textContent='Connection issue';if(!auth)showLogin()}}
$('loginForm').addEventListener('submit',async e=>{e.preventDefault();$('loginError').textContent='';auth=btoa(unescape(encodeURIComponent($('username').value))+':'+unescape(encodeURIComponent($('password').value)));sessionStorage.setItem('bcc_auth',auth);await loadStatus();if(!$('app').classList.contains('hidden'))$('password').value=''});
$('refresh').addEventListener('click',loadStatus);$('logout').addEventListener('click',()=>{auth='';sessionStorage.removeItem('bcc_auth');$('password').value='';showLogin()});
if(auth)loadStatus();else showLogin();setInterval(()=>{if(auth)loadStatus()},60000);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML, headers={"Cache-Control": "no-store",
                                                 "X-Content-Type-Options": "nosniff"})
