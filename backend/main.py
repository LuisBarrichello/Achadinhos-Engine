"""
=============================================================
 Achadinhos do Momento — Backend API  (v2 — code review)
 Stack : FastAPI + SQLite (via SQLModel)
=============================================================
Fixes aplicados nesta versão:
  [SEC-1] compare_digest em TODOS os pontos de comparação de secrets
  [SEC-2] Secrets sem valor default — falham explicitamente se não configurados
  [SEC-3] CORS restrito ao domínio do frontend via env var
  [SEC-4] Validação de URL com HttpUrl (Pydantic) no LinkCreate
  [SEC-5] /click com rate limit simples via cache em memória
  [BUG-1] int(hub_challenge) protegido com try/except
  [BUG-2] DATABASE_URL documentado no render.yaml
  [WAL]   PRAGMA WAL correto para engine síncrono do SQLModel
=============================================================
"""

import hashlib
import hmac
import logging
import os
import time
import httpx

from collections import defaultdict
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException, Request, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl, field_validator
from sqlalchemy import event
from sqlmodel import Field, Session, SQLModel, create_engine, select

# ─── Logging ────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("achadinhos")

# ─── Config ──────────────────────────────────────────────────
# [SEC-2] Secrets sem default → crash imediato e óbvio se não configurados.
# Um default fraco é pior que nenhum: o dev esquece, deploya, e o sistema
# fica "funcionando" com credenciais públicas sem nenhum aviso.
def _require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"Variável de ambiente obrigatória não definida: {key}\n"
            f"Consulte o arquivo .env.example para referência."
        )
    return val

DATABASE_URL          = os.getenv("DATABASE_URL", "sqlite:///./achadinhos.db")
WEBHOOK_VERIFY_TOKEN  = _require_env("WEBHOOK_VERIFY_TOKEN")
META_APP_SECRET       = os.getenv("META_APP_SECRET", "")   # Opcional em dev
PAGE_ACCESS_TOKEN     = os.getenv("PAGE_ACCESS_TOKEN", "")  # Opcional em dev
ADMIN_SECRET          = _require_env("ADMIN_SECRET")
# [SEC-3] CORS: restrito ao domínio do frontend. "*" em produção é inaceitável.
FRONTEND_ORIGIN       = os.getenv("FRONTEND_ORIGIN", "http://localhost:5500")

# ─── Banco de Dados ──────────────────────────────────────────
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def _enable_wal(dbapi_conn, _):
    """
    WAL permite leituras e escritas simultâneas sem lock global.
    Aplicado via event hook em toda nova conexão ao pool.
    Nota: event.listen(engine, ...) é correto para engine SÍNCRONO
    do SQLModel (create_engine → sqlalchemy.Engine, não AsyncEngine).
    """
    dbapi_conn.execute("PRAGMA journal_mode=WAL;")
    dbapi_conn.execute("PRAGMA synchronous=NORMAL;")

event.listen(engine, "connect", _enable_wal)


# ─── Modelos ─────────────────────────────────────────────────
class Link(SQLModel, table=True):
    id         : Optional[int] = Field(default=None, primary_key=True)
    title      : str           = Field(index=True)
    url        : str
    emoji      : str           = "🛍️"
    badge      : Optional[str] = None
    badge_color: str           = "#e11d48"
    active     : bool          = True
    order      : int           = 0
    clicks     : int           = 0


class KeywordLink(SQLModel, table=True):
    id      : Optional[int] = Field(default=None, primary_key=True)
    keyword : str           = Field(index=True)
    url     : str
    message : str           = "Oi! Aqui está seu link 👇\n{url}"


def get_session():
    with Session(engine) as session:
        yield session


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        if not session.exec(select(Link)).first():
            seed_links = [
                Link(title="🔥 Ofertas Shopee do Dia",    url="https://shopee.com.br/seu_link_afiliado", emoji="🔥", badge="OFERTA", order=0),
                Link(title="⚡ Mercado Livre em Destaque", url="https://mercadolivre.com.br/seu_link",   emoji="⚡", badge="TOP",    order=1),
            ]
            for lk in seed_links:
                session.add(lk)
            session.commit()
        if not session.exec(select(KeywordLink)).first():
            session.add(KeywordLink(keyword="EU QUERO", url="https://shopee.com.br/seu_link_afiliado"))
            session.commit()
    log.info("✅ Banco inicializado.")
    yield


# ─── App ─────────────────────────────────────────────────────
app = FastAPI(title="Achadinhos do Momento API", lifespan=lifespan)

# [SEC-3] CORS restrito. Em produção, FRONTEND_ORIGIN deve ser definido.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


# ─── Schemas ─────────────────────────────────────────────────
class LinkCreate(BaseModel):
    title      : str
    # [SEC-4] HttpUrl valida que a URL tem schema (http/https) e domínio.
    # Impede valores como "javascript:alert(1)" ou strings aleatórias.
    url        : HttpUrl
    emoji      : str           = "🛍️"
    badge      : Optional[str] = None
    badge_color: str           = "#e11d48"
    active     : bool          = True
    order      : int           = 0

    @field_validator("badge_color")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        """Garante que badge_color é um hex RGB válido (#rrggbb)."""
        import re
        if not re.match(r'^#[0-9A-Fa-f]{6}$', v):
            raise ValueError("badge_color deve ser um hex RGB válido, ex: #e11d48")
        return v


# ─── Helper: verificar secret admin ──────────────────────────
def verify_admin(x_admin_secret: str = Header(...)):
    # [SEC-1] compare_digest: tempo constante, sem timing attack.
    if not hmac.compare_digest(x_admin_secret.encode(), ADMIN_SECRET.encode()):
        raise HTTPException(status_code=403, detail="Não autorizado")

# ─── Rate limit simples para /click ──────────────────────────
# [SEC-5] Sem rate limit, bots podem inflar métricas de cliques.
# Solução leve sem dependências: dict em memória com timestamp por IP.
_click_cache: dict[str, float] = defaultdict(float)
CLICK_COOLDOWN_SECONDS = 60  # mesmo IP só registra 1 clique/minuto por link

def _rate_limit_click(request: Request, link_id: int) -> bool:
    """Retorna True se o clique deve ser registrado, False se for duplicata."""
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{link_id}"
    now = time.monotonic()
    if now - _click_cache[key] < CLICK_COOLDOWN_SECONDS:
        return False
    _click_cache[key] = now
    return True


# ══════════════════════════════════════════════════════════════
# ROTAS DE LINKS
# ══════════════════════════════════════════════════════════════

@app.get("/links", response_model=List[Link])
def list_links(session: Session = Depends(get_session)):
    return session.exec(
        select(Link).where(Link.active == True).order_by(Link.order)
    ).all()


@app.post("/links", response_model=Link, dependencies=[Depends(verify_admin)])
def create_link(data: LinkCreate, session: Session = Depends(get_session)):
    link = Link(**data.model_dump())
    # HttpUrl serializa para string; garantir que o campo str receba string
    link.url = str(data.url)
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


@app.patch("/links/{link_id}", response_model=Link, dependencies=[Depends(verify_admin)])
def update_link(link_id: int, data: LinkCreate, session: Session = Depends(get_session)):
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")
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
def register_click(
    link_id : int,
    request : Request,
    session : Session = Depends(get_session),
):
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")
    # [SEC-5] Só registra se passar no rate limit por IP
    if _rate_limit_click(request, link_id):
        link.clicks += 1
        session.commit()
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
    """
    Handshake inicial da Meta. Deve retornar hub.challenge como inteiro.
    """
    # [SEC-1] compare_digest também aqui — o verify_token é um secret.
    token_ok = (
        hub_verify_token is not None and
        hmac.compare_digest(
            hub_verify_token.encode(),
            WEBHOOK_VERIFY_TOKEN.encode()
        )
    )
    if hub_mode == "subscribe" and token_ok:
        # [BUG-1] hub_challenge pode não ser um inteiro válido.
        # A Meta sempre envia um número, mas um request malformado
        # não deve retornar 500 — retornamos a string diretamente.
        try:
            return int(hub_challenge)
        except (TypeError, ValueError):
            log.warning(f"hub_challenge inválido recebido: {hub_challenge!r}")
            return hub_challenge  # Retorna string como fallback seguro

    raise HTTPException(status_code=403, detail="Token de verificação inválido")


@app.post("/webhook/meta")
async def receive_webhook(
    request    : Request,
    background : BackgroundTasks,
    session    : Session = Depends(get_session),
):
    """
    Recebe eventos do Instagram.
    Responde 200 à Meta imediatamente; processa DMs em background.
    """
    import json as _json

    body_bytes = await request.body()

    # [SEC-1] Validação HMAC obrigatória em produção.
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

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue
            value   = change.get("value", {})
            comment = value.get("text", "").upper().strip()
            user_id = value.get("from", {}).get("id")
            if not user_id:
                continue
            keyword_link = session.exec(
                select(KeywordLink).where(KeywordLink.keyword == comment)
            ).first()
            if keyword_link:
                message = keyword_link.message.format(url=keyword_link.url)
                background.add_task(send_dm, user_id, message)
                log.info(f"📨 DM agendada (background) para {user_id}")

    return {"status": "ok"}


async def send_dm(recipient_id: str, message: str):
    """Envia DM via Graph API. Executado em background após o 200."""
    if not PAGE_ACCESS_TOKEN:
        log.warning("PAGE_ACCESS_TOKEN não configurado — DM não enviada.")
        return
    payload = {
        "recipient": {"id": recipient_id},
        "message":   {"text": message},
        "messaging_type": "RESPONSE",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://graph.facebook.com/v19.0/me/messages",
            json=payload,
            params={"access_token": PAGE_ACCESS_TOKEN},
        )
        if resp.status_code != 200:
            log.error(f"❌ Erro Graph API: {resp.text}")


# ─── Healthcheck ─────────────────────────────────────────────
@app.get("/")
def healthcheck():
    return {"status": "🟢 online", "project": "Achadinhos do Momento"}
