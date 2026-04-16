import hashlib
import hmac
import json
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from api.dependencies import verify_admin
from core.config import META_APP_SECRET, WEBHOOK_VERIFY_TOKEN
from core.database import engine, get_session
from models.domain import (
    EventStatusUpdate,
    KeywordLink,
    Link,
    WebhookEvent,
    WebhookEventRead,
)
from services.dm_counter import _increment_dm_today
from services.matching import _keyword_matches

log = logging.getLogger("achadinhos")

router = APIRouter(tags=["webhooks"])


# ── Helpers de fila ───────────────────────────────────────────────────────────

def _enqueue_dm(user_id: str, message: str) -> None:
    with Session(engine) as session:
        event = WebhookEvent(user_id=user_id, message=message)
        session.add(event)
        session.commit()
    _increment_dm_today()
    log.info(f"📥 DM enfileirada para {user_id}")


def _resolve_dm_message(raw_text: str) -> Optional[str]:
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

            message = _resolve_dm_message(raw_text)
            if message:
                _enqueue_dm(user_id, message)


# ── Rotas ─────────────────────────────────────────────────────────────────────

@router.get("/webhook/meta")
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


@router.post("/webhook/meta")
async def receive_webhook(request: Request):
    """
    [DQ-2] Sem BackgroundTasks — processa síncrono e salva na fila.
    Retorna 200 em < 500ms (exigência da Meta).
    """
    body_bytes = await request.body()

    if META_APP_SECRET:
        signature = request.headers.get("x-hub-signature-256", "")
        expected  = "sha256=" + hmac.new(
            META_APP_SECRET.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=403, detail="Assinatura inválida")

    try:
        payload = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Payload inválido")

    _process_and_enqueue(payload)
    return {"status": "ok"}


@router.get(
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


@router.patch(
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
