"""
=============================================================
 Achadinhos do Momento — Backend API  (v7 — DB Queue)
 Stack : FastAPI + PostgreSQL (via SQLModel + psycopg2)
=============================================================
Mudanças v7:
  [DQ-1] WebhookEvent — fila de DMs persistida no PostgreSQL.
          O webhook salva (user_id, message) como "pending" e
          retorna 200 imediatamente. O garimpeiro drena a fila
          de forma assíncrona, garantindo zero perda de dados
          em restarts do container (Render Free hiberna).
  [DQ-2] Remove BackgroundTasks do FastAPI — era in-memory,
          perdia dados em restarts silenciosamente.
  [DQ-3] Novos endpoints de fila (admin-only):
          GET  /webhooks/events/pending
          PATCH /webhooks/events/{id}
=============================================================
"""

import hashlib
import hmac
import logging
import os
import re
import time
import unicodedata
import httpx
from fastapi.responses import FileResponse
from pathlib import Path
from fastapi import HTTPException
from dotenv import load_dotenv
from datetime import date

from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl, field_validator
from sqlmodel import Field, Session, SQLModel, create_engine, select

load_dotenv()

# ─── Logging ────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("achadinhos")

# ─── Config ──────────────────────────────────────────────────
def _require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"Variável de ambiente obrigatória não definida: {key}\n"
            f"Consulte o arquivo .env.example para referência."
        )
    return val

DATABASE_URL         = _require_env("DATABASE_URL")
WEBHOOK_VERIFY_TOKEN = _require_env("WEBHOOK_VERIFY_TOKEN")
META_APP_SECRET      = os.getenv("META_APP_SECRET", "")
PAGE_ACCESS_TOKEN    = os.getenv("PAGE_ACCESS_TOKEN", "")
ADMIN_SECRET         = _require_env("ADMIN_SECRET")
FRONTEND_ORIGIN      = os.getenv("FRONTEND_ORIGIN", "http://localhost:5500")

# ─── Banco de Dados (PostgreSQL) ─────────────────────────────
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=300,
)

# ─── Contador de DMs em memória (fallback — fonte verdade é a tabela) ──
_dm_counters: dict[str, int] = {}

def _increment_dm_today() -> None:
    today = str(date.today())
    _dm_counters[today] = _dm_counters.get(today, 0) + 1

def _dm_count_today() -> int:
    return _dm_counters.get(str(date.today()), 0)


# ─── Modelos ─────────────────────────────────────────────────
class Link(SQLModel, table=True):
    id          : Optional[int] = Field(default=None, primary_key=True)
    title       : str           = Field(index=True)
    url         : str
    emoji       : str           = "🛍️"
    badge       : Optional[str] = None
    badge_color : str           = "#e11d48"
    active      : bool          = True
    order       : int           = 0
    clicks      : int           = 0
    image_url   : Optional[str] = None
    keyword     : Optional[str] = Field(default=None, index=True)


class KeywordLink(SQLModel, table=True):
    id      : Optional[int] = Field(default=None, primary_key=True)
    keyword : str           = Field(index=True)
    url     : str
    message : str           = "Oi! Aqui está seu link 👇\n{url}"


class WebhookEvent(SQLModel, table=True):
    """
    [DQ-1] Fila persistente de DMs pendentes.
    Substitui BackgroundTasks (in-memory, perdia dados em restarts).
    O garimpeiro drena esta tabela de forma assíncrona.
    """
    __tablename__ = "webhook_events"
    id           : Optional[int] = Field(default=None, primary_key=True)
    user_id      : str
    message      : str
    status       : str           = Field(default="pending", index=True)
    created_at   : int           = Field(default_factory=lambda: int(time.time()))
    processed_at : Optional[int] = None
    error        : Optional[str] = None


def get_session():
    with Session(engine) as session:
        yield session


def _run_migrations():
    migrations = [
        "ALTER TABLE link ADD COLUMN IF NOT EXISTS image_url TEXT",
        "ALTER TABLE link ADD COLUMN IF NOT EXISTS keyword TEXT",
        # [DQ-1] Tabela de fila — idempotente, SQLModel.create_all cuida do resto
        """
        CREATE TABLE IF NOT EXISTS webhook_events (
            id           SERIAL PRIMARY KEY,
            user_id      TEXT    NOT NULL,
            message      TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'pending',
            created_at   INTEGER NOT NULL,
            processed_at INTEGER,
            error        TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_webhook_events_status ON webhook_events (status)",
    ]
    import sqlalchemy
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(sqlalchemy.text(sql))
                conn.commit()
                log.info(f"✅ Migration OK")
            except Exception as exc:
                log.debug(f"Migration ignorada ({exc})")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _run_migrations()
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        if not session.exec(select(Link)).first():
            seed_links = [
                Link(
                    title="🔥 Ofertas Shopee do Dia",
                    url="https://shopee.com.br/seu_link_afiliado",
                    emoji="🔥", badge="OFERTA", order=0, keyword="SHOPEE",
                ),
                Link(
                    title="⚡ Mercado Livre em Destaque",
                    url="https://mercadolivre.com.br/seu_link",
                    emoji="⚡", badge="TOP", order=1, keyword="MELI",
                ),
            ]
            for lk in seed_links:
                session.add(lk)
            session.commit()

        if not session.exec(select(KeywordLink)).first():
            session.add(KeywordLink(
                keyword="EU QUERO",
                url="https://shopee.com.br/seu_link_afiliado"
            ))
            session.commit()

    log.info("✅ Banco PostgreSQL inicializado.")
    yield


# ─── App ─────────────────────────────────────────────────────
app = FastAPI(title="Achadinhos do Momento API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════
# FUZZY MATCHING
# ══════════════════════════════════════════════════════════════

def _normalize_keyword(text: str) -> str:
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", text)
    ascii_text = nfd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]", "", ascii_text.upper())


def _keyword_matches(comment: str, keyword: str) -> bool:
    norm_comment = _normalize_keyword(comment)
    norm_keyword  = _normalize_keyword(keyword)
    if not norm_keyword:
        return False
    return norm_keyword in norm_comment


# ─── Schemas ─────────────────────────────────────────────────
class LinkCreate(BaseModel):
    title       : str
    url         : HttpUrl
    emoji       : str           = "🛍️"
    badge       : Optional[str] = None
    badge_color : str           = "#e11d48"
    active      : bool          = True
    order       : int           = 0
    image_url   : Optional[str] = None
    keyword     : Optional[str] = None

    @field_validator("badge_color")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        if not re.match(r'^#[0-9A-Fa-f]{6}$', v):
            raise ValueError("badge_color deve ser um hex RGB válido, ex: #e11d48")
        return v

    @field_validator("keyword")
    @classmethod
    def normalize_keyword(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return _normalize_keyword(v) or None

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = v.strip()
        if not re.match(r'^https?://', v):
            raise ValueError("image_url deve começar com http:// ou https://")
        return v


class LinkRead(BaseModel):
    id          : int
    title       : str
    url         : str
    emoji       : str
    badge       : Optional[str]
    badge_color : str
    active      : bool
    order       : int
    clicks      : int
    image_url   : Optional[str]
    keyword     : Optional[str]

    class Config:
        from_attributes = True


class WebhookEventRead(BaseModel):
    id         : int
    user_id    : str
    message    : str
    status     : str
    created_at : int

    class Config:
        from_attributes = True


class EventStatusUpdate(BaseModel):
    status : str                # pending | processing | completed | failed
    error  : Optional[str] = None


class SystemStatus(BaseModel):
    db_connected    : bool
    webhook_active  : bool
    dm_count_today  : int
    pending_dms     : int       # [DQ-1] fila visível no dashboard
    version         : str
    image_mode      : str


# ─── Helpers ─────────────────────────────────────────────────
def verify_admin(x_admin_secret: str = Header(...)):
    if not hmac.compare_digest(x_admin_secret.encode(), ADMIN_SECRET.encode()):
        raise HTTPException(status_code=403, detail="Não autorizado")


_click_cache: dict[str, float] = {}
CLICK_COOLDOWN_SECONDS = 60
_CLICK_CACHE_CLEANUP_EVERY = 500
_click_cache_inserts = 0


def _rate_limit_click(request: Request, link_id: int) -> bool:
    global _click_cache_inserts
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{link_id}"
    now = time.monotonic()
    if now - _click_cache.get(key, 0.0) < CLICK_COOLDOWN_SECONDS:
        return False
    _click_cache[key] = now
    _click_cache_inserts += 1
    if _click_cache_inserts >= _CLICK_CACHE_CLEANUP_EVERY:
        cutoff = now - CLICK_COOLDOWN_SECONDS
        expired = [k for k, ts in _click_cache.items() if ts < cutoff]
        for k in expired:
            del _click_cache[k]
        _click_cache_inserts = 0
    return True


# [DQ-1] Salva DM na fila em vez de disparar diretamente
def _enqueue_dm(user_id: str, message: str) -> None:
    with Session(engine) as session:
        event = WebhookEvent(user_id=user_id, message=message)
        session.add(event)
        session.commit()
    _increment_dm_today()
    log.info(f"📥 DM enfileirada para {user_id}")


# ══════════════════════════════════════════════════════════════
# ROTAS DE LINKS
# ══════════════════════════════════════════════════════════════

@app.get("/links", response_model=List[LinkRead])
def list_links(session: Session = Depends(get_session)):
    return session.exec(
        select(Link).where(Link.active == True).order_by(Link.order)
    ).all()


@app.post("/links", response_model=LinkRead, dependencies=[Depends(verify_admin)])
def create_link(data: LinkCreate, session: Session = Depends(get_session)):
    if data.keyword:
        existing = session.exec(
            select(Link).where(Link.keyword == data.keyword)
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Keyword '{data.keyword}' já usada por: '{existing.title}'"
            )
    link = Link(**data.model_dump())
    link.url = str(data.url)
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


@app.patch("/links/{link_id}", response_model=LinkRead, dependencies=[Depends(verify_admin)])
def update_link(link_id: int, data: LinkCreate, session: Session = Depends(get_session)):
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")
    if data.keyword:
        existing = session.exec(
            select(Link)
            .where(Link.keyword == data.keyword)
            .where(Link.id != link_id)
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Keyword '{data.keyword}' já usada por: '{existing.title}'"
            )
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(link, field, str(value) if field == "url" else value)
    session.commit()
    session.refresh(link)
    return link


@app.delete("/links/{link_id}", dependencies=[Depends(verify_admin)])
def delete_link(link_id: int, session: Session = Depends(get_session)):
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")
    session.delete(link)
    session.commit()
    return {"ok": True}


@app.post("/links/{link_id}/click")
def register_click(link_id: int, request: Request, session: Session = Depends(get_session)):
    import sqlalchemy as sa
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")
    if _rate_limit_click(request, link_id):
        session.exec(
            sa.update(Link)
            .where(Link.id == link_id)
            .values(clicks=Link.clicks + 1)
        )
        session.commit()
        session.refresh(link)
    return {"clicks": link.clicks}


# ══════════════════════════════════════════════════════════════
# FILA DE DMs  [DQ-1]
# ══════════════════════════════════════════════════════════════

@app.get(
    "/webhooks/events/pending",
    response_model=List[WebhookEventRead],
    dependencies=[Depends(verify_admin)],
    summary="[DQ-1] Retorna DMs pendentes para o garimpeiro processar",
)
def get_pending_events(
    limit  : int     = 50,
    session: Session = Depends(get_session),
):
    """
    O garimpeiro chama este endpoint periodicamente.
    Retorna até `limit` eventos com status 'pending', ordenados por data.
    """
    return session.exec(
        select(WebhookEvent)
        .where(WebhookEvent.status == "pending")
        .order_by(WebhookEvent.created_at)
        .limit(limit)
    ).all()


@app.patch(
    "/webhooks/events/{event_id}",
    dependencies=[Depends(verify_admin)],
    summary="[DQ-1] Atualiza status de um evento da fila",
)
def update_event_status(
    event_id : int,
    data     : EventStatusUpdate,
    session  : Session = Depends(get_session),
):
    """
    O garimpeiro chama com status='processing' ao iniciar,
    'completed' em sucesso ou 'failed' em falha.
    """
    valid = {"pending", "processing", "completed", "failed"}
    if data.status not in valid:
        raise HTTPException(status_code=400, detail=f"status deve ser um de: {valid}")

    event = session.get(WebhookEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Evento não encontrado")

    event.status = data.status
    if data.status in ("completed", "failed"):
        event.processed_at = int(time.time())
    if data.error:
        event.error = data.error[:500]
    session.commit()
    return {"ok": True, "id": event_id, "status": event.status}


# ══════════════════════════════════════════════════════════════
# WEBHOOK INSTAGRAM / META
# ══════════════════════════════════════════════════════════════

@app.get("/webhook/meta")
def verify_webhook(
    hub_mode         : Optional[str] = None,
    hub_challenge    : Optional[str] = None,
    hub_verify_token : Optional[str] = None,
):
    token_ok = (
        hub_verify_token is not None and
        hmac.compare_digest(
            hub_verify_token.encode(),
            WEBHOOK_VERIFY_TOKEN.encode()
        )
    )
    if hub_mode == "subscribe" and token_ok:
        try:
            return int(hub_challenge)
        except (TypeError, ValueError):
            return hub_challenge
    raise HTTPException(status_code=403, detail="Token de verificação inválido")


@app.post("/webhook/meta")
async def receive_webhook(request: Request):
    """
    [DQ-2] Sem BackgroundTasks — processa sincrono e salva na fila.
    Retorna 200 em < 500ms (exigência da Meta).
    """
    import json as _json

    body_bytes = await request.body()

    if META_APP_SECRET:
        signature = request.headers.get("x-hub-signature-256", "")
        expected  = "sha256=" + hmac.new(
            META_APP_SECRET.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=403, detail="Assinatura inválida")

    try:
        payload = _json.loads(body_bytes)
    except _json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Payload inválido")

    # [DQ-1] Enfileira sincronamente (DB write é rápido) e retorna 200
    _process_and_enqueue(payload)
    return {"status": "ok"}


def _process_and_enqueue(payload: dict) -> None:
    """
    [DQ-1] Resolve fuzzy matching e grava na fila do banco.
    Executa em < 100ms (apenas leituras e uma escrita no DB).
    """
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue

            value    = change.get("value", {})
            raw_text = value.get("text", "")
            user_id  = value.get("from", {}).get("id")

            if not user_id or not raw_text:
                continue

            log.info(f"📩 Comentário de {user_id}: '{raw_text[:60]}'")

            # Resolve mensagem via keyword
            message = _resolve_dm_message(raw_text)
            if message:
                _enqueue_dm(user_id, message)  # [DQ-1] salva no banco


def _resolve_dm_message(raw_text: str) -> Optional[str]:
    """Retorna a mensagem de DM se alguma keyword bater, senão None."""
    with Session(engine) as session:
        # 1. Links com keyword inline
        links_com_keyword = session.exec(
            select(Link)
            .where(Link.keyword != None)  # noqa: E711
            .where(Link.active == True)
        ).all()

        matched_link = next(
            (lk for lk in links_com_keyword if _keyword_matches(raw_text, lk.keyword)),
            None
        )
        if matched_link:
            return (
                f"Oi! Obrigado pelo interesse! 🛍️\n"
                f"Aqui está o link do produto:\n{matched_link.url}"
            )

        # 2. Tabela legada KeywordLink
        kw_links = session.exec(select(KeywordLink)).all()
        legacy = next(
            (kl for kl in kw_links if _keyword_matches(raw_text, kl.keyword)),
            None
        )
        if legacy:
            return legacy.message.format(url=legacy.url)

    return None


# ══════════════════════════════════════════════════════════════
# STATUS / ADMIN
# ══════════════════════════════════════════════════════════════

@app.get("/status", response_model=SystemStatus)
def system_status(session: Session = Depends(get_session)):
    db_ok = False
    try:
        import sqlalchemy
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        log.error(f"DB health check falhou: {exc}")

    # [DQ-1] Conta pendentes para exibir no dashboard
    pending = 0
    try:
        pending = session.exec(
            select(WebhookEvent).where(WebhookEvent.status == "pending")
        ).all().__len__()
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


@app.get("/")
def healthcheck():
    db_ok = False
    try:
        import sqlalchemy
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


@app.get("/admin", include_in_schema=False)
def painel_admin():
    caminho_admin = Path(__file__).parent / "static" / "admin.html"
    if not caminho_admin.exists():
        raise HTTPException(status_code=404, detail="admin.html não encontrado em static/.")
    return FileResponse(caminho_admin)
