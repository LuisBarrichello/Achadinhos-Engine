"""
=============================================================
 Achadinhos do Momento — Backend API  (v6 — URL-only images)
 Stack : FastAPI + PostgreSQL (via SQLModel + psycopg2)
=============================================================
Mudanças v6 (Fase 1 — item 4):
  [IMG-URL] Remove toda lógica de upload físico de imagens.
             O campo image_url agora é apenas uma string de URL
             passada diretamente no corpo JSON do POST/PATCH.
             Nenhum arquivo é salvo localmente; nenhum serviço
             de terceiros (ImgBB etc.) é chamado.
  [DM-CTR]  Contador de DMs enviadas hoje (para o dashboard).
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

from fastapi import FastAPI, Header, HTTPException, Request, Depends, BackgroundTasks
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

# ─── Contador de DMs em memória (reseta ao reiniciar o processo) ──
# Para persistência entre restarts, mova para o banco.
_dm_counters: dict[str, int] = {}   # chave: "YYYY-MM-DD", valor: contagem

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
    # [IMG-URL] Apenas a URL da imagem — nenhum arquivo local
    image_url   : Optional[str] = None
    keyword     : Optional[str] = Field(default=None, index=True)


class KeywordLink(SQLModel, table=True):
    """Mantido por compatibilidade — nova lógica usa Link.keyword."""
    id      : Optional[int] = Field(default=None, primary_key=True)
    keyword : str           = Field(index=True)
    url     : str
    message : str           = "Oi! Aqui está seu link 👇\n{url}"


def get_session():
    with Session(engine) as session:
        yield session


def _run_migrations():
    migrations = [
        "ALTER TABLE link ADD COLUMN IF NOT EXISTS image_url TEXT",
        "ALTER TABLE link ADD COLUMN IF NOT EXISTS keyword TEXT",
    ]
    import sqlalchemy
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(sqlalchemy.text(sql))
                conn.commit()
                log.info(f"✅ Migration OK: {sql}")
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
    # [IMG-URL] Campo simples de URL de imagem — sem upload, sem FormData
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
        """[IMG-URL] Valida que a URL de imagem é http/https, se fornecida."""
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


# ─── Rota de status do sistema (para o dashboard) ────────────
class SystemStatus(BaseModel):
    db_connected    : bool
    webhook_active  : bool
    dm_count_today  : int
    version         : str
    image_mode      : str


# ─── Helper: verificar secret admin ──────────────────────────
def verify_admin(x_admin_secret: str = Header(...)):
    if not hmac.compare_digest(x_admin_secret.encode(), ADMIN_SECRET.encode()):
        raise HTTPException(status_code=403, detail="Não autorizado")


# ─── Rate limit simples para /click ──────────────────────────
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
    # [IMG-URL] Sem arquivo local para deletar — só remove o registro
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
async def receive_webhook(
    request    : Request,
    background : BackgroundTasks,
):
    """
    Retorna 200 OK imediatamente — processamento roda em background.
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

    background.add_task(_process_webhook_payload, payload)
    return {"status": "ok"}


async def _process_webhook_payload(payload: dict) -> None:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue

            value    = change.get("value", {})
            raw_text = value.get("text", "")
            user_id  = value.get("from", {}).get("id")

            if not user_id or not raw_text:
                continue

            log.info(f"📩 Comentário de {user_id}: '{raw_text}'")

            with Session(engine) as session:
                links_com_keyword = session.exec(
                    select(Link)
                    .where(Link.keyword != None)  # noqa: E711
                    .where(Link.active == True)
                ).all()

                matched_link = next(
                    (lk for lk in links_com_keyword
                     if _keyword_matches(raw_text, lk.keyword)),
                    None
                )

            if matched_link:
                message = (
                    f"Oi! Obrigado pelo interesse! 🛍️\n"
                    f"Aqui está o link do produto:\n{matched_link.url}"
                )
                await send_dm(user_id, message)
                _increment_dm_today()
                log.info(
                    f"📨 DM enviada para {user_id} "
                    f"→ keyword '{matched_link.keyword}' → link #{matched_link.id}"
                )
                continue

            with Session(engine) as session:
                kw_links = session.exec(select(KeywordLink)).all()
                legacy = next(
                    (kl for kl in kw_links
                     if _keyword_matches(raw_text, kl.keyword)),
                    None
                )

            if legacy:
                message = legacy.message.format(url=legacy.url)
                await send_dm(user_id, message)
                _increment_dm_today()
                log.info(f"📨 DM (legado) enviada para {user_id}")


async def send_dm(recipient_id: str, message: str):
    if not PAGE_ACCESS_TOKEN:
        log.warning("PAGE_ACCESS_TOKEN não configurado — DM não enviada.")
        return
    payload = {
        "recipient"      : {"id": recipient_id},
        "message"        : {"text": message},
        "messaging_type" : "RESPONSE",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://graph.facebook.com/v19.0/me/messages",
            json=payload,
            params={"access_token": PAGE_ACCESS_TOKEN},
        )
        if resp.status_code != 200:
            log.error(f"❌ Graph API erro: {resp.text[:150]}")


# ──────────────────────────────────────────────────────────────
# ROTA: Status do Sistema (para o dashboard de admin)
# ──────────────────────────────────────────────────────────────

@app.get("/status", response_model=SystemStatus)
def system_status():
    """
    Retorna indicadores de saúde usados pelo dashboard Admin.
    Não requer autenticação — não expõe dados sensíveis.
    """
    db_ok = False
    try:
        import sqlalchemy
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        log.error(f"DB health check falhou: {exc}")

    return SystemStatus(
        db_connected   = db_ok,
        webhook_active = bool(WEBHOOK_VERIFY_TOKEN and META_APP_SECRET),
        dm_count_today = _dm_count_today(),
        version        = "6.0.0",
        image_mode     = "url-only",
    )


# ─── Healthcheck ─────────────────────────────────────────────
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
        "version"            : "6.0.0",
        "image_mode"         : "url-only",   # [IMG-URL]
        "keyword_automation" : "enabled",
        "fuzzy_matching"     : "enabled",
    }


@app.get("/admin", include_in_schema=False)
def painel_admin():
    caminho_admin = Path(__file__).parent / "static" / "admin.html"
    if not caminho_admin.exists():
        raise HTTPException(status_code=404, detail="admin.html não encontrado em static/.")
    return FileResponse(caminho_admin)
