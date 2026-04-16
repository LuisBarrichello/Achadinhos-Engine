import logging
from pathlib import Path

import sqlalchemy
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from core.config import META_APP_SECRET, WEBHOOK_VERIFY_TOKEN
from core.database import engine, get_session
from models.domain import SystemStatus, WebhookEvent
from services.dm_counter import _dm_count_today

log = logging.getLogger("achadinhos")

router = APIRouter(tags=["system"])

# backend/api/routes/system.py → três níveis acima = backend/
_BACKEND_DIR = Path(__file__).parent.parent.parent


@router.get("/status", response_model=SystemStatus)
def system_status(session: Session = Depends(get_session)):
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        log.error(f"DB health check falhou: {exc}")

    pending = 0
    try:
        pending = len(session.exec(
            select(WebhookEvent).where(WebhookEvent.status == "pending")
        ).all())
    except Exception:
        pass

    return SystemStatus(
        db_connected   = db_ok,
        webhook_active = bool(WEBHOOK_VERIFY_TOKEN and META_APP_SECRET),
        dm_count_today = _dm_count_today(),
        pending_dms    = pending,
        version        = "7.0.0",
        image_mode     = "url-only",
    )


@router.get("/")
def healthcheck():
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        log.error(f"DB health check falhou: {exc}")

    return {
        "status"             : "🟢 online" if db_ok else "🔴 db_error",
        "db_connected"       : db_ok,
        "project"            : "Achadinhos do Momento",
        "version"            : "7.0.0",
        "image_mode"         : "url-only",
        "keyword_automation" : "enabled",
        "dm_queue"           : "db-backed",   # [DQ-1]
    }


@router.get("/admin", include_in_schema=False)
def painel_admin():
    caminho_admin = _BACKEND_DIR / "static" / "admin.html"
    if not caminho_admin.exists():
        raise HTTPException(status_code=404, detail="admin.html não encontrado em static/.")
    return FileResponse(caminho_admin)
