"""
dashboard/server.py — FastAPI backend for the bot control dashboard.

Endpoints
─────────
GET  /api/status          — bot latency, guild count, cog list
GET  /api/files           — list editable files (cogs/ + config.py)
GET  /api/file?path=...   — read a file's source
POST /api/file            — write + hot-reload
GET  /api/logs            — last N log lines (reads bot_dashboard.log)
POST /api/reload-cog      — reload a specific cog by extension name
GET  /                    — serve the dashboard SPA
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import hmac
import hashlib
import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config

if TYPE_CHECKING:
    from main import MyBot

log = logging.getLogger("dashboard")

ROOT = Path(__file__).parent.parent
COGS_DIR = ROOT / "cogs"
EDITABLE_ROOTS = [COGS_DIR, ROOT / "config.py"]

LOG_FILE = ROOT / "bot_dashboard.log"

# ─── Auth ─────────────────────────────────────────────────────────────────────

def verify_secret(x_dashboard_secret: str = Header(...)):
    if x_dashboard_secret != config.DASHBOARD_SECRET:
        raise HTTPException(status_code=401, detail="Invalid dashboard secret.")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _safe_path(raw: str) -> Path:
    """Resolve path and ensure it sits inside the project root."""
    p = (ROOT / raw).resolve()
    if not str(p).startswith(str(ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Path traversal denied.")
    return p


def _list_editable() -> list[str]:
    files = []
    for py in sorted(COGS_DIR.glob("*.py")):
        files.append(str(py.relative_to(ROOT)))
    files.append("config.py")
    return files


# ─── App factory ──────────────────────────────────────────────────────────────

def create_app(bot: "MyBot") -> FastAPI:
    app = FastAPI(title="Bot Dashboard", docs_url=None, redoc_url=None)

    # ── Status ────────────────────────────────────────────────────────────────
    @app.get("/api/status")
    async def status(_=Depends(verify_secret)):
        return {
            "latency_ms": round(bot.latency * 1000, 1),
            "guild_count": len(bot.guilds),
            "cogs": list(bot.cogs.keys()),
            "user": str(bot.user) if bot.user else "connecting...",
        }

    # ── File list ─────────────────────────────────────────────────────────────
    @app.get("/api/files")
    async def list_files(_=Depends(verify_secret)):
        return {"files": _list_editable()}

    # ── Read file ─────────────────────────────────────────────────────────────
    @app.get("/api/file")
    async def read_file(path: str, _=Depends(verify_secret)):
        p = _safe_path(path)
        if not p.exists():
            raise HTTPException(status_code=404, detail="File not found.")
        return {"path": path, "content": p.read_text(encoding="utf-8")}

    # ── Write + hot-reload ────────────────────────────────────────────────────
    class WriteBody(BaseModel):
        path: str
        content: str

    @app.post("/api/file")
    async def write_file(body: WriteBody, _=Depends(verify_secret)):
        p = _safe_path(body.path)
        if str(p.relative_to(ROOT)) not in _list_editable():
            raise HTTPException(status_code=403, detail="File not in editable list.")

        # Syntax-check before writing
        try:
            compile(body.content, str(p), "exec")
        except SyntaxError as exc:
            raise HTTPException(status_code=422, detail=f"SyntaxError: {exc}")

        p.write_text(body.content, encoding="utf-8")
        log.info("File written: %s", body.path)

        reload_result = "not reloaded"

        # Hot-reload config
        if body.path == "config.py":
            try:
                importlib.reload(config)
                reload_result = "config reloaded"
            except Exception as exc:
                reload_result = f"config reload failed: {exc}"

        # Hot-reload cog
        elif body.path.startswith("cogs/") and body.path.endswith(".py"):
            stem = Path(body.path).stem
            ext = f"cogs.{stem}"
            try:
                bot.reload_extension(ext)
                reload_result = f"{ext} reloaded"
            except Exception as exc:
                # Try fresh load if not previously loaded
                try:
                    bot.load_extension(ext)
                    reload_result = f"{ext} loaded (new)"
                except Exception as exc2:
                    reload_result = f"reload failed: {exc2}"

        return {"status": "ok", "reload": reload_result}

    # ── Reload cog ────────────────────────────────────────────────────────────
    class ReloadBody(BaseModel):
        extension: str   # e.g. "cogs.reporting"

    @app.post("/api/reload-cog")
    async def reload_cog(body: ReloadBody, _=Depends(verify_secret)):
        try:
            bot.reload_extension(body.extension)
            return {"status": "ok", "detail": f"{body.extension} reloaded"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Log tail ──────────────────────────────────────────────────────────────
    @app.get("/api/logs")
    async def get_logs(lines: int = 200, _=Depends(verify_secret)):
        if not LOG_FILE.exists():
            return {"lines": []}
        all_lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"lines": all_lines[-lines:]}

    # ── GitHub Webhook ────────────────────────────────────────────────────────
    @app.post("/api/webhook/github")
    async def github_webhook(request: Request, x_hub_signature_256: str = Header(None)):
        if not getattr(config, "GITHUB_WEBHOOK_SECRET", ""):
            raise HTTPException(status_code=500, detail="Webhook secret not configured.")
        if not x_hub_signature_256:
            raise HTTPException(status_code=401, detail="Missing signature.")
            
        payload = await request.body()
        
        # Verify signature
        mac = hmac.new(config.GITHUB_WEBHOOK_SECRET.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
        expected_mac = "sha256=" + mac.hexdigest()
        if not hmac.compare_digest(expected_mac, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid signature.")
            
        log.info("Received valid GitHub webhook. Pulling updates...")
        
        try:
            process = await asyncio.create_subprocess_shell(
                "git pull",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                log.error("git pull failed: %s", stderr.decode())
                raise HTTPException(status_code=500, detail="Git pull failed.")
                
            log.info("git pull successful: %s", stdout.decode())
        except Exception as exc:
            log.exception("Exception during git pull: %s", exc)
            raise HTTPException(status_code=500, detail="Internal error during git pull.")
            
        # Schedule restart
        async def do_restart():
            await asyncio.sleep(2)
            log.warning("Shutting down for update via GitHub webhook...")
            os._exit(0)
            
        asyncio.create_task(do_restart())
        
        return {"status": "ok", "detail": "Update pulled. Restarting..."}

    # ── SPA ───────────────────────────────────────────────────────────────────
    dashboard_html = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    async def spa():
        return dashboard_html

    return app
