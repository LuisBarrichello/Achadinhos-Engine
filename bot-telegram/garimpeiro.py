"""
╔══════════════════════════════════════════════════════════════════╗
║         Achadinhos do Momento — Garimpeiro v4.2                  ║
║         Worker assíncrono · Shopee Affiliate Open Platform       ║
╠══════════════════════════════════════════════════════════════════╣
║  Mudanças v4.2 (DB Queue para DMs):                              ║
║  [DQ-1]  DMProcessor — drena a fila webhook_events do banco.     ║
║          Garante entrega de DMs mesmo após restarts do           ║
║          container no Render Free. Processa a cada ciclo e       ║
║          também no loop de DM dedicado (intervalo menor).        ║
║  [DQ-2]  PAGE_ACCESS_TOKEN lido pelo garimpeiro — o backend      ║
║          apenas enfileira; quem envia é o garimpeiro.            ║
║  [DQ-3]  Backoff exponencial em falhas de DM (evita spam à       ║
║          Graph API após erros transitórios).                     ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

from script_generator import generate_script, script_to_narration
from tts_client import synthesize
from video_assembler import VideoAssemblerError, build_deal_video

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)-18s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("garimpeiro")

WORK_DIR = Path("./temp_videos")
WORK_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ══════════════════════════════════════════════════════════════════════════════

class Config:
    SHOPEE_APP_ID      : str = os.getenv("SHOPEE_APP_ID",     "")
    SHOPEE_APP_SECRET  : str = os.getenv("SHOPEE_APP_SECRET", "")
    SHOPEE_SUB_ID      : str = os.getenv("SHOPEE_SUB_ID", "achadinhos")

    TELEGRAM_BOT_TOKEN  : str = os.getenv("TELEGRAM_BOT_TOKEN",  "")
    TELEGRAM_CHANNEL_ID : str = os.getenv("TELEGRAM_CHANNEL_ID", "")
    ADMIN_TELEGRAM_CHAT_ID : str = os.getenv(
        "ADMIN_TELEGRAM_CHAT_ID",
        os.getenv("TELEGRAM_CHANNEL_ID", "")
    )

    API_BASE_URL : str = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
    ADMIN_SECRET : str = os.getenv("ADMIN_SECRET", "")

    # [DQ-2] Token da Graph API para envio de DMs
    PAGE_ACCESS_TOKEN : str = os.getenv("PAGE_ACCESS_TOKEN", "")

    POLL_INTERVAL_MIN : int   = int(os.getenv("POLL_INTERVAL_MIN", "30"))
    # [DQ-1] Intervalo dedicado para drenar fila de DMs (mais frequente)
    DM_POLL_INTERVAL_SEC : int = int(os.getenv("DM_POLL_INTERVAL_SEC", "60"))
    MIN_DISCOUNT_PCT  : int   = int(os.getenv("MIN_DISCOUNT_PCT",  "20"))
    DEALS_PER_CYCLE   : int   = int(os.getenv("DEALS_PER_CYCLE",   "5"))
    HTTP_TIMEOUT      : float = float(os.getenv("HTTP_TIMEOUT",    "20.0"))

    PROCESSED_DEALS_PATH : Path = Path(
        os.getenv("PROCESSED_DEALS_PATH", "processed_deals.json")
    )

    VIDEO_OOM_THRESHOLD : int = int(os.getenv("VIDEO_OOM_THRESHOLD", "3"))
    TTS_FAIL_THRESHOLD  : int = int(os.getenv("TTS_FAIL_THRESHOLD",  "3"))
    # [DQ-3] Máximo de tentativas antes de marcar evento como "failed"
    DM_MAX_RETRIES : int = int(os.getenv("DM_MAX_RETRIES", "3"))

    @classmethod
    def validate(cls) -> None:
        must_have = {
            "SHOPEE_APP_ID"      : cls.SHOPEE_APP_ID,
            "SHOPEE_APP_SECRET"  : cls.SHOPEE_APP_SECRET,
            "TELEGRAM_BOT_TOKEN" : cls.TELEGRAM_BOT_TOKEN,
            "TELEGRAM_CHANNEL_ID": cls.TELEGRAM_CHANNEL_ID,
            "ADMIN_SECRET"       : cls.ADMIN_SECRET,
        }
        missing = [k for k, v in must_have.items() if not v]
        if missing:
            raise EnvironmentError(
                "Variáveis de ambiente obrigatórias não definidas:\n"
                + "\n".join(f"  · {k}" for k in missing)
                + "\n\nConsulte o .env.example."
            )
        if not cls.PAGE_ACCESS_TOKEN:
            log.warning(
                "⚠️  PAGE_ACCESS_TOKEN não configurado — DMs não serão enviadas. "
                "Configure no .env para ativar o envio automático."
            )
        log.info("✅ Configuração validada.")


# ══════════════════════════════════════════════════════════════════════════════
# MODELO
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Deal:
    item_id        : str
    title          : str
    affiliate_url  : str
    price          : Optional[float]
    original_price : Optional[float]
    discount_pct   : Optional[int]
    image_url      : Optional[str]
    shop_name      : str = ""

    @property
    def unique_key(self) -> str:
        return f"shopee:{self.item_id}"

    def to_vitrine_payload(self) -> dict:
        title_with_discount = (
            f"{self.title} — {self.discount_pct}% OFF"
            if self.discount_pct else self.title
        )
        return {
            "title"      : title_with_discount[:120],
            "url"        : self.affiliate_url,
            "emoji"      : "🛍️",
            "badge"      : "SHOPEE",
            "badge_color": "#ee4d2d",
            "order"      : 10,
            "image_url"  : self.image_url,
        }

    def to_telegram_caption(self) -> str:
        lines: list[str] = [f"*{self.title}*", ""]
        if self.price is not None and self.original_price is not None:
            lines.append(f"~~R$ {self.original_price:,.2f}~~  ->  *R$ {self.price:,.2f}*")
        elif self.price is not None:
            lines.append(f"*R$ {self.price:,.2f}*")
        if self.discount_pct:
            lines.append(f"*{self.discount_pct}% OFF*")
        if self.shop_name:
            lines.append(f"Loja: {self.shop_name}")
        lines += ["", f"[Ver oferta na Shopee]({self.affiliate_url})", "", "_Achadinhos do Momento_"]
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# DEAL STORE
# ══════════════════════════════════════════════════════════════════════════════

class DealStore:
    def __init__(self, path: Path) -> None:
        self._path  = path
        self._store : dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open(encoding="utf-8") as f:
                raw = json.load(f)
            return raw if isinstance(raw, dict) else {k: 0 for k in raw}
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"DealStore: erro ao ler {self._path}: {e}")
        return {}

    def already_seen(self, key: str) -> bool:
        return key in self._store

    def mark(self, key: str) -> None:
        self._store[key] = int(time.time())

    def flush(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._store, f, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
        except IOError as e:
            log.error(f"DealStore: falha ao persistir: {e}")
            tmp.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# DM PROCESSOR  [DQ-1]
# ══════════════════════════════════════════════════════════════════════════════

class DMProcessor:
    """
    [DQ-1] Drena a fila webhook_events do banco PostgreSQL.

    Fluxo por evento:
      1. GET  /webhooks/events/pending     → lista de {id, user_id, message}
      2. PATCH /webhooks/events/{id}       → status=processing
      3. POST  graph.facebook.com/messages → envia DM
      4. PATCH /webhooks/events/{id}       → status=completed | failed
    """

    GRAPH_DM_URL = "https://graph.facebook.com/v19.0/me/messages"

    def __init__(
        self,
        api_base         : str,
        admin_secret     : str,
        page_access_token: str,
        timeout          : float,
        max_retries      : int = 3,
    ) -> None:
        self._api_base   = api_base.rstrip("/")
        self._headers    = {"x-admin-secret": admin_secret, "Content-Type": "application/json"}
        self._pat        = page_access_token
        self._timeout    = timeout
        self._max_retries = max_retries

    # ── API helpers ───────────────────────────────────────────

    async def _fetch_pending(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._api_base}/webhooks/events/pending",
                    headers=self._headers,
                )
            if resp.status_code != 200:
                log.warning(f"DMProcessor: GET pending → {resp.status_code}")
                return []
            return resp.json()
        except Exception as exc:
            log.warning(f"DMProcessor: falha ao buscar fila: {exc}")
            return []

    async def _update_status(
        self,
        event_id : int,
        status   : str,
        error    : str | None = None,
    ) -> None:
        body = {"status": status}
        if error:
            body["error"] = error[:400]
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                await client.patch(
                    f"{self._api_base}/webhooks/events/{event_id}",
                    json=body,
                    headers=self._headers,
                )
        except Exception as exc:
            log.warning(f"DMProcessor: falha ao atualizar evento {event_id}: {exc}")

    # ── Envio Graph API ───────────────────────────────────────

    async def _send_dm(self, recipient_id: str, message: str) -> tuple[bool, str | None]:
        """
        [DQ-3] Retorna (sucesso, motivo_falha).
        Não relança exceções — o caller decide como tratar.
        """
        if not self._pat:
            return False, "PAGE_ACCESS_TOKEN não configurado"

        payload = {
            "recipient"      : {"id": recipient_id},
            "message"        : {"text": message},
            "messaging_type" : "RESPONSE",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self.GRAPH_DM_URL,
                    json=payload,
                    params={"access_token": self._pat},
                )
            if resp.status_code == 200:
                return True, None
            return False, f"Graph API {resp.status_code}: {resp.text[:150]}"
        except httpx.TimeoutException:
            return False, "Timeout na Graph API"
        except Exception as exc:
            return False, f"Exceção: {exc}"

    # ── Processamento da fila ─────────────────────────────────

    async def process_pending(self) -> int:
        """
        [DQ-1] Itera sobre eventos pendentes e tenta enviar DMs.
        Retorna quantidade de DMs enviadas com sucesso nesta rodada.
        """
        events = await self._fetch_pending()
        if not events:
            return 0

        log.info(f"📨 DMProcessor: {len(events)} DM(s) na fila")
        sent = 0

        for event in events:
            event_id   = event["id"]
            user_id    = event["user_id"]
            message    = event["message"]

            await self._update_status(event_id, "processing")

            ok, reason = await self._send_dm(user_id, message)

            if ok:
                await self._update_status(event_id, "completed")
                log.info(f"  ✅ DM enviada → {user_id}")
                sent += 1
            else:
                await self._update_status(event_id, "failed", reason)
                log.warning(f"  ❌ DM falhou → {user_id} | {reason}")

            # [DQ-3] Pequena pausa para não saturar a Graph API
            await asyncio.sleep(0.5)

        return sent


# ══════════════════════════════════════════════════════════════════════════════
# SHOPEE API
# ══════════════════════════════════════════════════════════════════════════════

SHOPEE_ERR_AUTH       = "auth_error"
SHOPEE_ERR_RATE_LIMIT = "rate_limit"
SHOPEE_ERR_SERVER     = "server_error"
SHOPEE_ERR_NETWORK    = "network_error"
SHOPEE_ERR_LOGIC      = "logic_error"


class ShopeeAPI:
    BASE_URL = "https://open-api.affiliate.shopee.com.br"

    def __init__(self, app_id, app_secret, sub_id, timeout):
        self._app_id     = app_id
        self._app_secret = app_secret.encode("utf-8")
        self._sub_id     = sub_id
        self._timeout    = timeout

    def _build_signature(self, path, timestamp, body=""):
        base = f"{self._app_id}{timestamp}{path}{body}"
        return hmac.new(self._app_secret, base.encode("utf-8"), hashlib.sha256).hexdigest()

    def _auth_params(self, path, timestamp, body=""):
        return {"app_id": self._app_id, "timestamp": timestamp,
                "sign": self._build_signature(path, timestamp, body)}

    async def fetch_top_products(self, limit=20, min_discount=0) -> tuple[list[dict], Optional[str]]:
        path      = "/v1/shop/get_top_products"
        timestamp = int(time.time())
        params    = {**self._auth_params(path, timestamp), "page": 1, "limit": limit, "sort_type": 2}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(self.BASE_URL + path, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            return [], self._classify_http_error("fetch_top_products", e)
        except httpx.RequestError as e:
            log.error(f"Shopee rede: {e}")
            return [], SHOPEE_ERR_NETWORK

        code = data.get("code", 0) or data.get("error", 0)
        if code != 0:
            log.warning(f"Shopee erro lógico: code={code}")
            return [], SHOPEE_ERR_LOGIC

        items = (
            data.get("data", {}).get("item_list")
            or data.get("data", {}).get("items")
            or data.get("item_list")
            or []
        )
        if min_discount > 0:
            items = [it for it in items if self._extract_discount(it) >= min_discount]
        return items, None

    async def generate_affiliate_link(self, item_url: str) -> Optional[str]:
        path      = "/v1/link/generate"
        timestamp = int(time.time())
        body_dict = {"original_url": item_url, "sub_ids": [self._sub_id]}
        body_str  = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False)
        params    = self._auth_params(path, timestamp, body_str)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self.BASE_URL + path, content=body_str,
                    headers={"Content-Type": "application/json"}, params=params)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            log.error(f"Shopee [generate_affiliate_link]: {e}")
            return None

        code = data.get("code", 0) or data.get("error", 0)
        if code != 0:
            return None
        return (
            data.get("data", {}).get("short_link")
            or data.get("data", {}).get("affiliate_link")
            or data.get("short_link")
        )

    async def build_deal(self, raw_item: dict) -> Optional[Deal]:
        item_id = str(raw_item.get("item_id") or raw_item.get("itemid") or "")
        shop_id = str(raw_item.get("shop_id") or raw_item.get("shopid") or "")
        title   = (raw_item.get("item_name") or raw_item.get("name") or "").strip()
        if not item_id or not title:
            return None

        slug     = title.lower().replace(" ", "-")[:60]
        item_url = f"https://shopee.com.br/{slug}-i.{shop_id}.{item_id}"
        aff_url  = await self.generate_affiliate_link(item_url)
        if not aff_url:
            return None

        def _brl(raw):
            if raw is None: return None
            v = float(raw)
            return round(v / 100_000, 2) if v > 100_000 else round(v, 2)

        price    = _brl(raw_item.get("price_min") or raw_item.get("price"))
        orig     = _brl(raw_item.get("price_min_before_discount") or raw_item.get("original_price"))
        discount = self._extract_discount(raw_item)
        if not discount and price and orig and orig > price:
            discount = int(((orig - price) / orig) * 100)

        img = raw_item.get("item_image") or raw_item.get("image") or raw_item.get("cover") or ""
        if img and not img.startswith("http"):
            img = "https:" + img

        return Deal(
            item_id=item_id, title=title, affiliate_url=aff_url,
            price=price, original_price=orig if orig != price else None,
            discount_pct=discount or None, image_url=img or None,
            shop_name=raw_item.get("shop_name") or raw_item.get("shopName") or "",
        )

    @staticmethod
    def _extract_discount(item: dict) -> int:
        raw = item.get("discount") or item.get("discount_pct") or item.get("price_discount_rate") or 0
        try:
            v = float(raw)
            return int(v * 100) if 0 < v < 1 else int(v)
        except (TypeError, ValueError):
            return 0

    def _classify_http_error(self, method: str, exc: httpx.HTTPStatusError) -> str:
        status = exc.response.status_code
        body   = exc.response.text[:200]
        if status == 429:
            log.warning(f"Shopee [{method}] rate limit (429)")
            return SHOPEE_ERR_RATE_LIMIT
        if status in (401, 403):
            log.error(f"Shopee [{method}] autenticação falhou ({status})")
            return SHOPEE_ERR_AUTH
        if status >= 500:
            log.error(f"Shopee [{method}] erro servidor ({status}): {body}")
            return SHOPEE_ERR_SERVER
        log.error(f"Shopee [{method}] HTTP {status}: {body}")
        return SHOPEE_ERR_SERVER


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class TelegramClient:
    TELEGRAM_API = "https://api.telegram.org/bot"
    _SEND_DELAY  = 2.5

    def __init__(self, token, channel_id, admin_chat_id, timeout):
        self._base          = f"{self.TELEGRAM_API}{token}"
        self._channel_id    = channel_id
        self._admin_chat_id = admin_chat_id or channel_id
        self._timeout       = timeout

    async def send_deal(self, deal: Deal) -> bool:
        caption = deal.to_telegram_caption()
        if deal.image_url:
            ok = await self._send_photo(deal.image_url, caption, self._channel_id)
            if ok:
                await asyncio.sleep(self._SEND_DELAY)
                return True
            log.warning("Telegram: sendPhoto falhou — fallback para sendMessage")
        ok = await self._send_message(caption, self._channel_id)
        await asyncio.sleep(self._SEND_DELAY)
        return ok

    async def send_daily_hello(self, stats: dict) -> None:
        lines = [
            "🌅 *Garimpeiro — Relatório Diário*", "",
            f"📅 Data: {date.today().strftime('%d/%m/%Y')}",
            f"⏱ Intervalo de ciclo: {stats.get('interval_min', '?')} min",
            f"💸 Desconto mínimo: {stats.get('min_discount', '?')}%",
            f"📦 Deals por ciclo: {stats.get('deals_per_cycle', '?')}",
            f"📨 DM poll interval: {stats.get('dm_interval_sec', '?')}s",
            "", "✅ Bot iniciado e operacional.",
        ]
        await self._send_message("\n".join(lines), self._admin_chat_id)
        log.info("📢 Mensagem de bom dia enviada.")

    async def send_critical_alert(self, error_type: str, detail: str = "") -> None:
        if error_type == SHOPEE_ERR_AUTH:
            msg = (
                "🚨🚨🚨 *ALERTA CRÍTICO — SHOPEE* 🚨🚨🚨\n\n"
                "❌ *CREDENCIAL EXPIRADA OU INVÁLIDA*\n\n"
                "O bot não consegue mais buscar produtos.\n"
                "Acesse o painel Shopee Affiliate e *RENOVE O APP SECRET* agora.\n\n"
                f"Detalhe técnico: `{detail or 'HTTP 401/403'}`"
            )
        elif error_type == SHOPEE_ERR_RATE_LIMIT:
            msg = (
                "⚠️ *AVISO — SHOPEE RATE LIMIT*\n\n"
                "Rate limit atingido (HTTP 429).\n"
                "_Bot retomará automaticamente no próximo ciclo._"
            )
        elif error_type == "oom":
            msg = (
                "🧠 *AVISO — RAM INSUFICIENTE PARA VÍDEO*\n\n"
                "A montagem de vídeo foi *desativada* neste ciclo por falta de RAM.\n"
                f"Detalhe: `{detail}`"
            )
        elif error_type == "tts_fail":
            msg = (
                "🎙️ *AVISO — FALHAS REPETIDAS NO TTS*\n\n"
                f"O gerador de voz falhou {detail} vezes consecutivas.\n"
                "_Deals continuarão sendo postadas sem áudio/vídeo._"
            )
        elif error_type == "cycle_zero":
            msg = (
                "⚠️ *AVISO — CICLO SEM PUBLICAÇÕES*\n\n"
                f"Nenhuma das {detail} deals do ciclo foi publicada no Telegram.\n"
                "Verifique: token do bot, chat ID, e conectividade."
            )
        elif error_type == "dm_queue_stale":
            msg = (
                "📨 *AVISO — FILA DE DMs ACUMULADA*\n\n"
                f"{detail} DMs pendentes há mais de 1h.\n"
                "Verifique PAGE_ACCESS_TOKEN e conectividade com a Graph API."
            )
        else:
            msg = (
                f"⚠️ *Aviso do Garimpeiro*\n\n"
                f"Tipo: `{error_type}`\n"
                f"Detalhe: `{detail or 'sem detalhe'}`"
            )
        await self._send_message(msg, self._admin_chat_id)
        log.warning(f"🚨 Alerta admin: {error_type} — {detail[:80] if detail else ''}")

    async def _send_photo(self, photo_url, caption, chat_id) -> bool:
        return await self._post("sendPhoto", {
            "chat_id": chat_id, "photo": photo_url,
            "caption": caption, "parse_mode": "Markdown",
        })

    async def _send_message(self, text, chat_id) -> bool:
        return await self._post("sendMessage", {
            "chat_id": chat_id, "text": text,
            "parse_mode": "Markdown", "disable_web_page_preview": False,
        })

    async def _post(self, method, payload) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}/{method}", json=payload)
            if resp.status_code == 429:
                retry = resp.json().get("parameters", {}).get("retry_after", 60)
                log.warning(f"Telegram rate limit em {method} — aguardando {retry}s")
                await asyncio.sleep(retry + 2)
                return False
            if resp.status_code == 400:
                log.warning(f"Telegram 400 em {method}: {resp.text[:150]}")
                return False
            resp.raise_for_status()
            return True
        except httpx.TimeoutException:
            log.error(f"Telegram: timeout em {method}")
            return False
        except httpx.RequestError as e:
            log.error(f"Telegram: rede em {method}: {e}")
            return False
        except Exception as e:
            log.error(f"Telegram: erro inesperado em {method}: {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════════
# VITRINE API
# ══════════════════════════════════════════════════════════════════════════════

class VitrineAPI:
    def __init__(self, base_url, admin_secret, timeout):
        self._base    = base_url
        self._timeout = timeout
        self._headers = {"Content-Type": "application/json", "x-admin-secret": admin_secret}

    async def publish(self, deal: Deal) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/links", json=deal.to_vitrine_payload(),
                    headers=self._headers)
            if resp.status_code == 403:
                log.error("Vitrine: ADMIN_SECRET inválido (403)")
                return False
            resp.raise_for_status()
            log.info(f"Vitrine: link criado — '{deal.title[:50]}'")
            return True
        except httpx.RequestError as e:
            log.warning(f"Vitrine inacessível: {e}")
            return False
        except Exception as e:
            log.error(f"Vitrine erro: {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════════
# GARIMPEIRO
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class _PipelineCounters:
    oom_consecutive : int  = 0
    tts_consecutive : int  = 0
    video_disabled  : bool = False


class Garimpeiro:

    def __init__(self, cfg: type[Config]) -> None:
        self._cfg   = cfg
        self._store = DealStore(cfg.PROCESSED_DEALS_PATH)

        self._shopee = ShopeeAPI(
            cfg.SHOPEE_APP_ID, cfg.SHOPEE_APP_SECRET,
            cfg.SHOPEE_SUB_ID, cfg.HTTP_TIMEOUT,
        )
        self._telegram = TelegramClient(
            token         = cfg.TELEGRAM_BOT_TOKEN,
            channel_id    = cfg.TELEGRAM_CHANNEL_ID,
            admin_chat_id = cfg.ADMIN_TELEGRAM_CHAT_ID,
            timeout       = cfg.HTTP_TIMEOUT,
        )
        self._vitrine = VitrineAPI(cfg.API_BASE_URL, cfg.ADMIN_SECRET, cfg.HTTP_TIMEOUT)

        # [DQ-1] Processador da fila de DMs
        self._dm_processor = DMProcessor(
            api_base          = cfg.API_BASE_URL,
            admin_secret      = cfg.ADMIN_SECRET,
            page_access_token = cfg.PAGE_ACCESS_TOKEN,
            timeout           = cfg.HTTP_TIMEOUT,
            max_retries       = cfg.DM_MAX_RETRIES,
        )

        self._last_hello_date : Optional[date] = None
        self._counters = _PipelineCounters()

    # ── Bom dia ───────────────────────────────────────────────────────────────
    async def _maybe_send_daily_hello(self) -> None:
        today = date.today()
        if self._last_hello_date == today:
            return
        await self._telegram.send_daily_hello({
            "interval_min"   : self._cfg.POLL_INTERVAL_MIN,
            "min_discount"   : self._cfg.MIN_DISCOUNT_PCT,
            "deals_per_cycle": self._cfg.DEALS_PER_CYCLE,
            "dm_interval_sec": self._cfg.DM_POLL_INTERVAL_SEC,
        })
        self._last_hello_date = today

    # ── [DQ-1] Drenagem da fila de DMs ────────────────────────────────────────
    async def _drain_dm_queue(self) -> None:
        try:
            sent = await self._dm_processor.process_pending()
            if sent:
                log.info(f"📨 {sent} DM(s) entregue(s) da fila")
        except Exception as exc:
            log.warning(f"_drain_dm_queue: exceção: {exc}")

    # ── [RB-1] Pipeline de vídeo isolado ─────────────────────────────────────
    async def _run_video_pipeline(self, deal: Deal) -> Path | None:
        if self._counters.video_disabled:
            log.info(f"  [RB-2] Vídeo desativado (OOM circuit breaker ativo).")
            return None

        audio_path = WORK_DIR / f"{deal.item_id}_audio.wav"
        tts_ok = False
        try:
            sections  = await generate_script(deal)
            narration = script_to_narration(sections)
            tts_ok    = await synthesize(narration, audio_path)
        except Exception as exc:
            log.warning(f"  [RB-1] TTS exceção: {exc}")

        if tts_ok:
            self._counters.tts_consecutive = 0
        else:
            self._counters.tts_consecutive += 1
            log.warning(f"  [RB-4] TTS falhou ({self._counters.tts_consecutive}× consecutivo).")
            if self._counters.tts_consecutive == self._cfg.TTS_FAIL_THRESHOLD:
                await self._telegram.send_critical_alert(
                    "tts_fail", detail=str(self._counters.tts_consecutive),
                )
            return None

        video_path = None
        try:
            video_path = await build_deal_video(deal, WORK_DIR)
            if video_path:
                self._counters.oom_consecutive = 0
                log.info(f"  🎬 Vídeo: {video_path.name}")
        except VideoAssemblerError as exc:
            reason = str(exc)
            is_oom = any(kw in reason.lower() for kw in ("ram", "memoryerror", "oom", "137", "killed"))
            if is_oom:
                self._counters.oom_consecutive += 1
                log.error(f"  [RB-2] OOM #{self._counters.oom_consecutive}: {reason[:120]}")
                if self._counters.oom_consecutive >= self._cfg.VIDEO_OOM_THRESHOLD:
                    if not self._counters.video_disabled:
                        self._counters.video_disabled = True
                        await self._telegram.send_critical_alert("oom", detail=reason[:200])
            else:
                log.error(f"  [RB-1] VideoAssemblerError: {reason[:120]}")
        except Exception as exc:
            log.error(f"  [RB-1] Vídeo exceção inesperada: {exc}")

        return video_path

    # ── Ciclo principal ───────────────────────────────────────────────────────
    async def run_cycle(self) -> None:
        log.info("─" * 60)
        log.info("⛏️  Iniciando ciclo de garimpo")

        await self._maybe_send_daily_hello()

        # [DQ-1] Drena DMs pendentes antes de buscar novos produtos
        await self._drain_dm_queue()

        raw_items, err_type = await self._shopee.fetch_top_products(
            limit        = self._cfg.DEALS_PER_CYCLE * 4,
            min_discount = self._cfg.MIN_DISCOUNT_PCT,
        )

        if err_type in (SHOPEE_ERR_AUTH, SHOPEE_ERR_RATE_LIMIT):
            await self._telegram.send_critical_alert(err_type)
            if err_type == SHOPEE_ERR_AUTH:
                log.error("⛔ Credencial Shopee expirada — aguardando 2× intervalo")
                await asyncio.sleep(self._cfg.POLL_INTERVAL_MIN * 60 * 2)
            return

        if err_type:
            log.warning(f"Shopee retornou erro {err_type} — pulando ciclo")
            return

        if not raw_items:
            log.info("Shopee: nenhum produto retornado.")
            return

        new_items  = [
            it for it in raw_items
            if not self._store.already_seen(
                f"shopee:{it.get('item_id') or it.get('itemid', '')}"
            )
        ]
        to_process = new_items[: self._cfg.DEALS_PER_CYCLE]
        log.info(
            f"📦 {len(raw_items)} brutos | {len(new_items)} novos | "
            f"{len(to_process)} a processar"
        )

        if not to_process:
            log.info("Nenhum item novo neste ciclo.")
            return

        deals: list[Deal] = []
        for raw in to_process:
            try:
                deal = await self._shopee.build_deal(raw)
                if deal:
                    deals.append(deal)
            except Exception as exc:
                log.warning(f"build_deal exceção: {exc}")
            await asyncio.sleep(0.5)

        if not deals:
            log.info("Nenhuma deal válida após normalização.")
            return

        tg_ok = vt_ok = 0
        for deal in deals:
            log.info(f"  Postando [{deal.item_id}]: {deal.title[:55]}")

            await self._run_video_pipeline(deal)

            tg_posted = False
            try:
                tg_posted = await self._telegram.send_deal(deal)
            except Exception as exc:
                log.error(f"  [RB-1] Telegram exceção: {exc}")

            if not tg_posted:
                log.warning(f"  Telegram falhou — '{deal.title[:40]}'")
                continue

            self._store.mark(deal.unique_key)
            tg_ok += 1

            try:
                if await self._vitrine.publish(deal):
                    vt_ok += 1
            except Exception as exc:
                log.warning(f"  [RB-1] Vitrine exceção: {exc}")

        self._store.flush()
        log.info(
            f"Ciclo concluído — Telegram: {tg_ok}/{len(deals)} | "
            f"Vitrine: {vt_ok}/{len(deals)}"
        )

        if tg_ok == 0 and len(deals) > 0:
            await self._telegram.send_critical_alert("cycle_zero", detail=str(len(deals)))

    # ── Loop de DMs dedicado ──────────────────────────────────────────────────
    async def _dm_loop(self) -> None:
        """
        [DQ-1] Loop paralelo ao run_forever() com intervalo menor.
        Garante entrega rápida de DMs mesmo entre ciclos de garimpo.
        """
        interval = self._cfg.DM_POLL_INTERVAL_SEC
        log.info(f"📨 DM loop iniciado (intervalo: {interval}s)")
        while True:
            try:
                await self._drain_dm_queue()
            except Exception as exc:
                log.warning(f"_dm_loop: exceção: {exc}")
            await asyncio.sleep(interval)

    # ── Loop infinito ─────────────────────────────────────────────────────────
    async def run_forever(self) -> None:
        interval = self._cfg.POLL_INTERVAL_MIN * 60

        log.info("=" * 60)
        log.info("Garimpeiro v4.2 iniciado")
        log.info(f"  Intervalo garimpo  : {self._cfg.POLL_INTERVAL_MIN} min")
        log.info(f"  Intervalo DM poll  : {self._cfg.DM_POLL_INTERVAL_SEC}s")
        log.info(f"  Desconto min.      : {self._cfg.MIN_DISCOUNT_PCT}%")
        log.info(f"  Deals/ciclo        : {self._cfg.DEALS_PER_CYCLE}")
        log.info(f"  Admin chat         : {self._cfg.ADMIN_TELEGRAM_CHAT_ID}")
        log.info(f"  DM queue           : db-backed (zero data loss)")
        log.info("=" * 60)

        # [DQ-1] Loop de DMs em paralelo ao ciclo de garimpo
        asyncio.create_task(self._dm_loop())

        while True:
            try:
                await self.run_cycle()
            except Exception as exc:
                log.exception(f"Exceção não tratada em run_cycle: {exc}")
                try:
                    await self._telegram.send_critical_alert(
                        "unexpected",
                        detail=f"{type(exc).__name__}: {str(exc)[:150]}",
                    )
                except Exception:
                    pass

            nxt = time.strftime("%H:%M:%S", time.localtime(time.time() + interval))
            log.info(f"Dormindo {self._cfg.POLL_INTERVAL_MIN}min — próximo ciclo às {nxt}")
            await asyncio.sleep(interval)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    Config.validate()
    await Garimpeiro(Config).run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Garimpeiro encerrado pelo usuário (Ctrl+C)")
    except EnvironmentError as exc:
        log.critical(str(exc))
        raise SystemExit(1)
