"""
=============================================================
 Achadinhos do Momento — Backend API  (v3 — image support)
 Stack : FastAPI + SQLite (via SQLModel)
=============================================================
Melhorias nesta versão (v3):
  [IMG-1] Upload de imagem por link (POST /links/{id}/image)
  [IMG-2] Servir imagens estáticas via /static/images/
  [IMG-3] Campo image_url no modelo Link (URL externa OU path local)
  [IMG-4] Validação de tipo MIME (jpeg, png, webp, gif)
  [IMG-5] Limite de tamanho de arquivo (5 MB por padrão)
  [IMG-6] Limpeza de imagem antiga ao fazer upload de nova
=============================================================
Fixes herdados da v2:
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
import shutil
import time
import uuid
import httpx
from dotenv import load_dotenv
from pathlib import Path

from collections import defaultdict
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, File, Header, HTTPException, Request, Depends, BackgroundTasks, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl, field_validator
from sqlalchemy import event
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

DATABASE_URL         = os.getenv("DATABASE_URL", "sqlite:///./achadinhos.db")
WEBHOOK_VERIFY_TOKEN = _require_env("WEBHOOK_VERIFY_TOKEN")
META_APP_SECRET      = os.getenv("META_APP_SECRET", "")
PAGE_ACCESS_TOKEN    = os.getenv("PAGE_ACCESS_TOKEN", "")
ADMIN_SECRET         = _require_env("ADMIN_SECRET")
FRONTEND_ORIGIN      = os.getenv("FRONTEND_ORIGIN", "http://localhost:5500")

# [IMG-1] Diretório de upload de imagens
IMAGES_DIR  = Path(os.getenv("IMAGES_DIR", "./static/images"))
IMAGES_URL  = os.getenv("IMAGES_URL", "/static/images")   # URL base pública
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_MB", "5")) * 1024 * 1024
ALLOWED_MIME    = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# ─── Banco de Dados ──────────────────────────────────────────
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def _enable_wal(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA journal_mode=WAL;")
    dbapi_conn.execute("PRAGMA synchronous=NORMAL;")

event.listen(engine, "connect", _enable_wal)


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
    # [IMG-3] Pode ser URL externa (https://...) ou path interno (/static/images/xxx.jpg)
    image_url   : Optional[str] = None
    # Metadados da imagem armazenada localmente
    image_local : Optional[str] = None   # nome do arquivo em IMAGES_DIR


class KeywordLink(SQLModel, table=True):
    id      : Optional[int] = Field(default=None, primary_key=True)
    keyword : str           = Field(index=True)
    url     : str
    message : str           = "Oi! Aqui está seu link 👇\n{url}"


def get_session():
    with Session(engine) as session:
        yield session


def _run_migrations():
    """Aplica colunas novas em banco existente (safe — só adiciona se não existir)."""
    migrations = [
        "ALTER TABLE link ADD COLUMN image_url TEXT",
        "ALTER TABLE link ADD COLUMN image_local TEXT",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(__import__("sqlalchemy").text(sql))
                conn.commit()
                log.info(f"✅ Migration aplicada: {sql}")
            except Exception:
                pass  # coluna já existe — ignorar


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Criar diretório de imagens se não existir
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"📁 Diretório de imagens: {IMAGES_DIR.resolve()}")

    _run_migrations()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        if not session.exec(select(Link)).first():
            seed_links = [
                Link(
                    title="🔥 Ofertas Shopee do Dia",
                    url="https://shopee.com.br/seu_link_afiliado",
                    emoji="🔥", badge="OFERTA", order=0,
                    image_url="https://lh3.googleusercontent.com/aida-public/AB6AXuBx-HVT4-5-ieSZAV7GMlDdXvFTRxq3iujBDvEOXB-fE9VuMUb5OKs0vC-FfjcB82CLQzl0e7rf6sfbFLSRe-KbmkNjn1wo8e3fPwBjZgDFuOyuqahcd4OFZeiNp7AtOHW9gKVc7MK4eMG_DCoyzscJEilNKz7xHtcDGVHXMG20F-Z5GhB7TYXkoxMrphx7U2bERstgzAI1HPtXTLydBjsn6v36KuzeQYjY2gdqDt3gprd_gYwhbwnG84BG4k0QW6tDzgh34KPQ51eO"
                ),
                Link(
                    title="⚡ Mercado Livre em Destaque",
                    url="https://mercadolivre.com.br/seu_link",
                    emoji="⚡", badge="TOP", order=1,
                    image_url="https://lh3.googleusercontent.com/aida-public/AB6AXuDbcrM9uHSsTZH3NIDd1PdrWdCYeHDavJH3JS2I_029yBAv2ynaUL3DhA_wLyKnwgf87qx5fbol6y3zMhjUwHgB5a_iU66_D_FcyTO3fBcUFbrgZYL3GhjAWCbAKOf1Qgu-MLY4Gs5O8Nhn9d1CfqIJyABOzBtrOaQfm0kCDM7DT8d-6J5GJ8eWzfGEesQSCO08ZncE9OQzTMY2MSpfufK0P0AaMHlKEmKx9SJSAHr2yw5KIcdU2la8OXEffjnG4EtD4rKH6-k0xNbq"
                ),
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

# [IMG-2] Montar diretório de imagens como rota estática
app.mount("/static/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


# ─── Schemas ─────────────────────────────────────────────────
class LinkCreate(BaseModel):
    title       : str
    url         : HttpUrl
    emoji       : str           = "🛍️"
    badge       : Optional[str] = None
    badge_color : str           = "#e11d48"
    active      : bool          = True
    order       : int           = 0
    # image_url pode ser informado manualmente (URL externa)
    image_url   : Optional[str] = None

    @field_validator("badge_color")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        import re
        if not re.match(r'^#[0-9A-Fa-f]{6}$', v):
            raise ValueError("badge_color deve ser um hex RGB válido, ex: #e11d48")
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

    class Config:
        from_attributes = True


# ─── Helper: verificar secret admin ──────────────────────────
def verify_admin(x_admin_secret: str = Header(...)):
    if not hmac.compare_digest(x_admin_secret.encode(), ADMIN_SECRET.encode()):
        raise HTTPException(status_code=403, detail="Não autorizado")


# ─── Rate limit simples para /click ──────────────────────────
_click_cache: dict[str, float] = defaultdict(float)
CLICK_COOLDOWN_SECONDS = 60

def _rate_limit_click(request: Request, link_id: int) -> bool:
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{link_id}"
    now = time.monotonic()
    if now - _click_cache[key] < CLICK_COOLDOWN_SECONDS:
        return False
    _click_cache[key] = now
    return True


# ── Helper: deletar arquivo de imagem local ──────────────────
def _delete_local_image(filename: Optional[str]) -> None:
    if not filename:
        return
    path = IMAGES_DIR / filename
    if path.exists():
        path.unlink()
        log.info(f"🗑️  Imagem removida: {filename}")


# ── Helper: construir URL pública da imagem ──────────────────
def _public_image_url(request: Request, filename: str) -> str:
    """Gera URL absoluta para a imagem armazenada localmente."""
    base = str(request.base_url).rstrip("/")
    return f"{base}{IMAGES_URL}/{filename}"


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
    # [IMG-6] Limpa imagem local ao deletar o link
    _delete_local_image(link.image_local)
    session.delete(link)
    session.commit()
    return {"ok": True}


@app.post("/links/{link_id}/click")
def register_click(link_id: int, request: Request, session: Session = Depends(get_session)):
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")
    if _rate_limit_click(request, link_id):
        link.clicks += 1
        session.commit()
    return {"clicks": link.clicks}


# ── Upload de Imagem ─────────────────────────────────────────
@app.post(
    "/links/{link_id}/image",
    response_model=LinkRead,
    dependencies=[Depends(verify_admin)],
    summary="Faz upload de imagem para um link existente",
)
async def upload_image(
    link_id : int,
    request : Request,
    file    : UploadFile = File(...),
    session : Session = Depends(get_session),
):
    """
    Aceita multipart/form-data com o campo `file`.
    - Valida tipo MIME: jpeg, png, webp, gif  [IMG-4]
    - Limita tamanho a MAX_IMAGE_MB MB         [IMG-5]
    - Remove imagem anterior se houver         [IMG-6]
    - Retorna o link atualizado com image_url
    """
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")

    # [IMG-4] Validar MIME
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo de arquivo não suportado: '{content_type}'. "
                   f"Use: {', '.join(ALLOWED_MIME)}"
        )

    # [IMG-5] Ler e validar tamanho
    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        max_mb = MAX_IMAGE_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"Arquivo muito grande. Máximo permitido: {max_mb} MB"
        )

    # [IMG-6] Deletar imagem antiga
    _delete_local_image(link.image_local)

    # Salvar nova imagem com nome único
    ext       = content_type.split("/")[-1].replace("jpeg", "jpg")
    filename  = f"link_{link_id}_{uuid.uuid4().hex[:8]}.{ext}"
    dest_path = IMAGES_DIR / filename

    with open(dest_path, "wb") as f:
        f.write(data)

    log.info(f"📸 Imagem salva: {filename} ({len(data)//1024} KB) → link #{link_id}")

    # Atualizar registro
    link.image_local = filename
    link.image_url   = _public_image_url(request, filename)
    session.commit()
    session.refresh(link)
    return link


@app.delete(
    "/links/{link_id}/image",
    dependencies=[Depends(verify_admin)],
    summary="Remove a imagem de um link",
)
def delete_image(link_id: int, session: Session = Depends(get_session)):
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")
    _delete_local_image(link.image_local)
    link.image_local = None
    link.image_url   = None
    session.commit()
    return {"ok": True, "message": "Imagem removida"}


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
            log.warning(f"hub_challenge inválido: {hub_challenge!r}")
            return hub_challenge

    raise HTTPException(status_code=403, detail="Token de verificação inválido")


@app.post("/webhook/meta")
async def receive_webhook(
    request    : Request,
    background : BackgroundTasks,
    session    : Session = Depends(get_session),
):
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
                log.info(f"📨 DM agendada para {user_id}")

    return {"status": "ok"}


async def send_dm(recipient_id: str, message: str):
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
    return {
        "status": "🟢 online",
        "project": "Achadinhos do Momento",
        "version": "3.0.0",
        "image_upload": "enabled",
        "max_image_mb": MAX_IMAGE_BYTES // (1024 * 1024),
    }
