"""
=============================================================
 Achadinhos do Momento — Backend API  (v4 — keyword per link)
 Stack : FastAPI + SQLite (via SQLModel)
=============================================================
Mudanças v4:
  [KW-1] Coluna `keyword` no modelo Link (ex: "FONE", "TENIS")
  [KW-2] LinkCreate/LinkRead incluem `keyword`
  [KW-3] Webhook busca keyword diretamente na tabela Link
  [KW-4] Migration automática da coluna keyword
=============================================================
"""

import hashlib
import hmac
import logging
import os
import re
import shutil
import time
import uuid
import httpx
from fastapi.responses import FileResponse
from pathlib import Path
from fastapi import HTTPException
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
IMAGES_URL  = os.getenv("IMAGES_URL", "/static/images")
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
    image_url   : Optional[str] = None
    image_local : Optional[str] = None
    # [KW-1] Palavra-chave única por produto, sempre em MAIÚSCULAS
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
    """Aplica colunas novas em banco existente (safe — só adiciona se não existir)."""
    migrations = [
        "ALTER TABLE link ADD COLUMN image_url TEXT",
        "ALTER TABLE link ADD COLUMN image_local TEXT",
        # [KW-4] Nova coluna para keyword por produto
        "ALTER TABLE link ADD COLUMN keyword TEXT",
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
                    emoji="🔥", badge="OFERTA", order=0, keyword="SHOPEE",
                    image_url="https://lh3.googleusercontent.com/aida-public/AB6AXuBx-HVT4-5-ieSZAV7GMlDdXvFTRxq3iujBDvEOXB-fE9VuMUb5OKs0vC-FfjcB82CLQzl0e7rf6sfbFLSRe-KbmkNjn1wo8e3fPwBjZgDFuOyuqahcd4OFZeiNp7AtOHW9gKVc7MK4eMG_DCoyzscJEilNKz7xHtcDGVHXMG20F-Z5GhB7TYXkoxMrphx7U2bERstgzAI1HPtXTLydBjsn6v36KuzeQYjY2gdqDt3gprd_gYwhbwnG84BG4k0QW6tDzgh34KPQ51eO"
                ),
                Link(
                    title="⚡ Mercado Livre em Destaque",
                    url="https://mercadolivre.com.br/seu_link",
                    emoji="⚡", badge="TOP", order=1, keyword="MELI",
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
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Achadinhos do Momento API", lifespan=lifespan)

app.mount("/static/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    image_url   : Optional[str] = None
    # [KW-2] Palavra-chave única — será salva em MAIÚSCULAS
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
        """Normaliza: maiúsculas, sem espaços extras, só alfanumérico."""
        if not v:
            return None
        return re.sub(r'[^\w]', '', v.upper().strip()) or None


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
    keyword     : Optional[str]  # [KW-2]

    class Config:
        from_attributes = True


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
        expired_keys = [k for k, ts in _click_cache.items() if ts < cutoff]
        for k in expired_keys:
            del _click_cache[k]
        _click_cache_inserts = 0
        log.debug(f"🧹 click_cache: removidas {len(expired_keys)} entradas expiradas, "
                  f"restam {len(_click_cache)}")

    return True


# ── Helper: deletar arquivo de imagem local ──────────────────
def _delete_local_image(filename: Optional[str]) -> None:
    if not filename:
        return
    path = IMAGES_DIR / filename
    try:
        path.unlink(missing_ok=True)
        log.info(f"🗑️  Imagem removida: {filename}")
    except OSError as e:
        log.error(f"❌ Não foi possível remover imagem '{filename}': {e}")


# ── Helper: construir URL pública da imagem ──────────────────
_PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "").rstrip("/")

def _public_image_url(request: Request, filename: str) -> str:
    base = _PUBLIC_API_URL or str(request.base_url).rstrip("/")
    return f"{base}{IMAGES_URL}/{filename}"


# ── Helper: limpar texto do comentário ───────────────────────
def _clean_comment(text: str) -> str:
    """Remove tudo que não é alfanumérico e coloca em maiúsculas."""
    return re.sub(r'[^\w]', '', text.upper().strip())


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
    # [KW-2] Verifica duplicidade de keyword antes de salvar
    if data.keyword:
        existing = session.exec(
            select(Link).where(Link.keyword == data.keyword)
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Palavra-chave '{data.keyword}' já está em uso pelo produto: '{existing.title}'"
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

    # [KW-2] Verifica duplicidade de keyword (ignora o próprio link)
    if data.keyword:
        existing = session.exec(
            select(Link)
            .where(Link.keyword == data.keyword)
            .where(Link.id != link_id)
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Palavra-chave '{data.keyword}' já está em uso pelo produto: '{existing.title}'"
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
    _delete_local_image(link.image_local)
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
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")

    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo de arquivo não suportado: '{content_type}'. "
                   f"Use: {', '.join(ALLOWED_MIME)}"
        )

    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        max_mb = MAX_IMAGE_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"Arquivo muito grande. Máximo permitido: {max_mb} MB"
        )

    _MAGIC: dict[str, list[bytes]] = {
        "image/jpeg": [b"\xff\xd8\xff"],
        "image/png":  [b"\x89PNG\r\n\x1a\n"],
        "image/gif":  [b"GIF87a", b"GIF89a"],
        "image/webp": [b"RIFF"],
    }
    expected_sigs = _MAGIC.get(content_type, [])
    if expected_sigs and not any(data.startswith(sig) for sig in expected_sigs):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Conteúdo do arquivo não corresponde ao tipo declarado '{content_type}'. "
                "O arquivo pode estar corrompido ou ser de um tipo diferente do informado."
            )
        )

    _delete_local_image(link.image_local)

    ext       = content_type.split("/")[-1].replace("jpeg", "jpg")
    filename  = f"link_{link_id}_{uuid.uuid4().hex[:8]}.{ext}"
    dest_path = IMAGES_DIR / filename

    with open(dest_path, "wb") as f:
        f.write(data)

    log.info(f"📸 Imagem salva: {filename} ({len(data)//1024} KB) → link #{link_id}")

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

            value      = change.get("value", {})
            raw_text   = value.get("text", "")
            user_id    = value.get("from", {}).get("id")

            if not user_id or not raw_text:
                continue

            # [KW-3] Limpa o comentário e busca keyword na tabela Link
            comment_clean = _clean_comment(raw_text)
            log.info(f"📩 Comentário recebido de {user_id}: '{raw_text}' → limpo: '{comment_clean}'")

            matched_link = session.exec(
                select(Link)
                .where(Link.keyword == comment_clean)
                .where(Link.active == True)
            ).first()

            if matched_link:
                message = f"Oi! Obrigado pelo interesse! 🛍️\nAqui está o link do produto:\n{matched_link.url}"
                background.add_task(send_dm, user_id, message)
                log.info(f"📨 DM agendada para {user_id} → keyword '{comment_clean}' → link #{matched_link.id}")
                continue

            # Fallback: mantém compatibilidade com tabela KeywordLink legada
            keyword_link = session.exec(
                select(KeywordLink).where(KeywordLink.keyword == comment_clean)
            ).first()
            if keyword_link:
                message = keyword_link.message.format(url=keyword_link.url)
                background.add_task(send_dm, user_id, message)
                log.info(f"📨 DM (legado) agendada para {user_id}")

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
        "version": "4.0.0",
        "image_upload": "enabled",
        "max_image_mb": MAX_IMAGE_BYTES // (1024 * 1024),
        "keyword_automation": "enabled",
    }


@app.get("/admin", include_in_schema=False)
def painel_admin():
    caminho_admin = Path(__file__).parent / "static" / "admin.html"
    if not caminho_admin.exists():
        raise HTTPException(status_code=404, detail="Arquivo admin.html não encontrado na pasta static.")
    return FileResponse(caminho_admin)
