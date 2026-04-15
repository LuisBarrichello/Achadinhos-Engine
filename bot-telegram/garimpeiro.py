"""
╔══════════════════════════════════════════════════════════════════╗
║         Achadinhos do Momento — Garimpeiro v5.0                  ║
║         Worker assíncrono · Render Free Background Worker        ║
╠══════════════════════════════════════════════════════════════════╣
║  Mudanças v5.0 (Desacoplamento total):                           ║
║  [V5-1]  Remove pipeline de vídeo (TTS/FFmpeg/script_generator). ║
║          Esses módulos rodam LOCALMENTE via local_video_worker.  ║
║          O garimpeiro no Render cuida apenas de dados e rede.    ║
║  [V5-2]  DMProcessor agora acessa o Neon DIRETAMENTE via         ║
║          psycopg2, eliminando round-trip HTTP desnecessário.     ║
║  [V5-3]  Webhook Meta migrado para Vercel (frontend/api/webhook) ║
║          — cold start no Render não afeta mais o webhook.        ║
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

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)-18s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("garimpeiro")

# NOTE: Sem imports de script_generator, tts_client ou video_assembler.
# [V5-1] Pipeline de vídeo roda em local_video_worker.py na máquina local.


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ══════════════════════════════════════════════════════════════════════════════

class Config:
    SHOPEE_APP_ID       : str = os.getenv("SHOPEE_APP_ID",      "")
    SHOPEE_APP_SECRET   : str = os.getenv("SHOPEE_APP_SECRET",  "")
    SHOPEE_SUB_ID       : str = os.getenv("SHOPEE_SUB_ID",      "achadinhos")

    TELEGRAM_BOT_TOKEN     : str = os.getenv("TELEGRAM_BOT_TOKEN",     "")
    TELEGRAM_CHANNEL_ID    : str = os.getenv("TELEGRAM_CHANNEL_ID",    "")
    ADMIN_TELEGRAM_CHAT_ID : str = os.getenv(
        "ADMIN_TELEGRAM_CHAT_ID",
        os.getenv("TELEGRAM_CHANNEL_ID", "")
    )

    # [V5-2] Acesso direto ao Neon — substitui chamadas HTTP ao FastAPI
    DATABASE_URL : str = os.getenv("DATABASE_URL", "")

    # Ainda usado para publicar links na vitrine
    API_BASE_URL : str = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
    ADMIN_SECRET : str = os.getenv("ADMIN_SECRET", "")

    # [V5-3] PAGE_ACCESS_TOKEN para Graph API (DMs)
    PAGE_ACCESS_TOKEN : str = os.getenv("PAGE_ACCESS_TOKEN", "")

    POLL_INTERVAL_MIN    : int   = int(os.getenv("POLL_INTERVAL_MIN",    "30"))
    DM_POLL_INTERVAL_SEC : int   = int(os.getenv("DM_POLL_INTERVAL_SEC", "60"))
    MIN_DISCOUNT_PCT     : int   = int(os.getenv("MIN_DISCOUNT_PCT",     "20"))
    DEALS_PER_CYCLE      : int   = int(os.getenv("DEALS_PER_CYCLE",      "5"))
    HTTP_TIMEOUT         : float = float(os.getenv("HTTP_TIMEOUT",       "20.0"))

    PROCESSED_DEALS_PATH : Path = Path(
        os.getenv("PROCESSED_DEALS_PATH", "processed_deals.json")
    )

    DM_MAX_RETRIES : int = int(os.getenv("DM_MAX_RETRIES", "3"))

    @classmethod
    def validate(cls) -> None:
        must_have = {
            "SHOPEE_APP_ID"      : cls.SHOPEE_APP_ID,
            "SHOPEE_APP_SECRET"  : cls.SHOPEE_APP_SECRET,
            "TELEGRAM_BOT_TOKEN" : cls.TELEGRAM_BOT_TOKEN,
            "TELEGRAM_CHANNEL_ID": cls.TELEGRAM_CHANNEL_ID,
            "ADMIN_SECRET"       : cls.ADMIN_SECRET,
            "DATABASE_URL"       : cls.DATABASE_URL,    # [V5-2]
        }
        missing = [k for k, v in must_have.items() if not v]
        if missing:
            raise EnvironmentError(
                "Variáveis de ambiente obrigatórias não definidas:\n"
                + "\n".join(f"  · {k}" for k in missing)
            )
        if not cls.PAGE_ACCESS_TOKEN:
            log.warning("⚠️  PAGE_ACCESS_TOKEN ausente — DMs não serão enviadas.")
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
# DM PROCESSOR  [V5-2] — Acesso direto ao Neon via psycopg2
# ══════════════════════════════════════════════════════════════════════════════

class DMProcessor:
    """
    [V5-2] Drena a tabela webhook_events diretamente via psycopg2.

    Elimina o round-trip HTTP:
      ANTES: garimpeiro → HTTP → FastAPI → Neon
      AGORA: garimpeiro → psycopg2 → Neon (direto)

    Mesmo banco, sem hop extra. Latência ~10ms vs ~200ms.
    """

    GRAPH_DM_URL = "https://graph.facebook.com/v19.0/me/messages"

    def __init__(
        self,
        database_url      : str,
        page_access_token : str,
        timeout           : float,
    ) -> None:
        self._database_url = database_url
        self._pat          = page_access_token
        self._timeout      = timeout

    def _get_conn(self):
        import psycopg2
        return psycopg2.connect(self._database_url)

    # ── DB helpers ────────────────────────────────────────────────────────

    def _fetch_pending(self, limit: int = 50) -> list[dict]:
        """Lê eventos pendentes diretamente do Neon."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, message
                    FROM webhook_events
                    WHERE status = 'pending'
                    ORDER BY created_at
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
            return [{"id": r[0], "user_id": r[1], "message": r[2]} for r in rows]
        finally:
            conn.close()

    def _update_status(
        self,
        event_id : int,
        status   : str,
        error    : Optional[str] = None,
    ) -> None:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                if status in ("completed", "failed"):
                    cur.execute(
                        """
                        UPDATE webhook_events
                        SET status=%s, processed_at=%s, error=%s
                        WHERE id=%s
                        """,
                        (status, int(time.time()), error[:500] if error else None, event_id),
                    )
                else:
                    cur.execute(
                        "UPDATE webhook_events SET status=%s WHERE id=%s",
                        (status, event_id),
                    )
            conn.commit()
        finally:
            conn.close()

    # ── Envio Graph API ───────────────────────────────────────────────────

    async def _send_dm(self, recipient_id: str, message: str) -> tuple[bool, str | None]:
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

    # ── Processamento ─────────────────────────────────────────────────────

    async def process_pending(self) -> int:
        """[V5-2] Lê Neon diretamente e envia DMs. Retorna DMs enviadas."""
        try:
            events = self._fetch_pending()
        except Exception as exc:
            log.warning(f"DMProcessor: falha ao buscar fila: {exc}")
            return 0

        if not events:
            return 0

        log.info(f"📨 DMProcessor: {len(events)} DM(s) na fila")
        sent = 0

        for event in events:
            event_id = event["id"]
            user_id  = event["user_id"]
            message  = event["message"]

            try:
                self._update_status(event_id, "processing")
            except Exception as exc:
                log.warning(f"  status→processing falhou: {exc}")

            ok, reason = await self._send_dm(user_id, message)

            try:
                if ok:
                    self._update_status(event_id, "completed")
                    log.info(f"  ✅ DM enviada → {user_id}")
                    sent += 1
                else:
                    self._update_status(event_id, "failed", reason)
                    log.warning(f"  ❌ DM falhou → {user_id} | {reason}")
            except Exception as exc:
                log.warning(f"  status→final falhou: {exc}")

            await asyncio.sleep(0.5)

        return sent


# ══════════════════════════════════════════════════════════════════════════════
# SHOPEE API  (inalterada)
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
# TELEGRAM CLIENT  (inalterado)
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
            "🌅 *Garimpeiro v5.0 — Relatório Diário*", "",
            f"📅 Data: {date.today().strftime('%d/%m/%Y')}",
            f"⏱ Intervalo de ciclo: {stats.get('interval_min', '?')} min",
            f"💸 Desconto mínimo: {stats.get('min_discount', '?')}%",
            f"📦 Deals por ciclo: {stats.get('deals_per_cycle', '?')}",
            f"📨 DM poll interval: {stats.get('dm_interval_sec', '?')}s",
            f"🎬 Vídeos: gerados localmente (local_video_worker.py)",
            f"🔗 Webhook: Vercel Serverless (sem cold start)",
            "", "✅ Bot iniciado e operacional.",
        ]
        await self._send_message("\n".join(lines), self._admin_chat_id)

    async def send_critical_alert(self, error_type: str, detail: str = "") -> None:
        msgs = {
            SHOPEE_ERR_AUTH: (
                "🚨🚨🚨 *ALERTA CRÍTICO — SHOPEE* 🚨🚨🚨\n\n"
                "❌ *CREDENCIAL EXPIRADA OU INVÁLIDA*\n\n"
                "Acesse o painel Shopee Affiliate e *RENOVE O APP SECRET* agora.\n\n"
                f"Detalhe: `{detail or 'HTTP 401/403'}`"
            ),
            SHOPEE_ERR_RATE_LIMIT: (
                "⚠️ *AVISO — SHOPEE RATE LIMIT*\n\n"
                "Rate limit atingido (HTTP 429).\n"
                "_Bot retomará automaticamente no próximo ciclo._"
            ),
            "cycle_zero": (
                f"⚠️ *AVISO — CICLO SEM PUBLICAÇÕES*\n\n"
                f"Nenhuma das {detail} deals do ciclo foi publicada no Telegram.\n"
                "Verifique: token do bot, chat ID, e conectividade."
            ),
        }
        msg = msgs.get(error_type, (
            f"⚠️ *Aviso do Garimpeiro v5*\n\n"
            f"Tipo: `{error_type}`\n"
            f"Detalhe: `{detail or 'sem detalhe'}`"
        ))
        await self._send_message(msg, self._admin_chat_id)
        log.warning(f"🚨 Alerta admin: {error_type}")

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
        except Exception as e:
            log.error(f"Telegram: erro em {method}: {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════════
# VITRINE API  (inalterada)
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
        except Exception as e:
            log.warning(f"Vitrine: {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════════
# GARIMPEIRO  [V5-1] Sem pipeline de vídeo
# ══════════════════════════════════════════════════════════════════════════════

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

        # [V5-2] Acesso direto ao Neon — sem HTTP interno
        self._dm_processor = DMProcessor(
            database_url      = cfg.DATABASE_URL,
            page_access_token = cfg.PAGE_ACCESS_TOKEN,
            timeout           = cfg.HTTP_TIMEOUT,
        )

        self._last_hello_date : Optional[date] = None

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

    async def _drain_dm_queue(self) -> None:
        try:
            sent = await self._dm_processor.process_pending()
            if sent:
                log.info(f"📨 {sent} DM(s) entregue(s)")
        except Exception as exc:
            log.warning(f"_drain_dm_queue: {exc}")

    async def run_cycle(self) -> None:
        log.info("─" * 60)
        log.info("⛏️  Iniciando ciclo de garimpo")

        await self._maybe_send_daily_hello()
        await self._drain_dm_queue()

        raw_items, err_type = await self._shopee.fetch_top_products(
            limit        = self._cfg.DEALS_PER_CYCLE * 4,
            min_discount = self._cfg.MIN_DISCOUNT_PCT,
        )

        if err_type in (SHOPEE_ERR_AUTH, SHOPEE_ERR_RATE_LIMIT):
            await self._telegram.send_critical_alert(err_type)
            if err_type == SHOPEE_ERR_AUTH:
                await asyncio.sleep(self._cfg.POLL_INTERVAL_MIN * 60 * 2)
            return

        if err_type or not raw_items:
            log.info(f"Shopee: {err_type or 'sem resultados'} — pulando ciclo")
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
                log.warning(f"build_deal: {exc}")
            await asyncio.sleep(0.5)

        if not deals:
            return

        tg_ok = vt_ok = 0
        for deal in deals:
            log.info(f"  Postando [{deal.item_id}]: {deal.title[:55]}")

            # [V5-1] Sem _run_video_pipeline aqui — vídeos gerados localmente
            tg_posted = False
            try:
                tg_posted = await self._telegram.send_deal(deal)
            except Exception as exc:
                log.error(f"  Telegram exceção: {exc}")

            if not tg_posted:
                log.warning(f"  Telegram falhou — '{deal.title[:40]}'")
                continue

            self._store.mark(deal.unique_key)
            tg_ok += 1

            try:
                if await self._vitrine.publish(deal):
                    vt_ok += 1
            except Exception as exc:
                log.warning(f"  Vitrine: {exc}")

        self._store.flush()
        log.info(f"Ciclo concluído — Telegram: {tg_ok}/{len(deals)} | Vitrine: {vt_ok}/{len(deals)}")

        if tg_ok == 0 and len(deals) > 0:
            await self._telegram.send_critical_alert("cycle_zero", detail=str(len(deals)))

    async def _dm_loop(self) -> None:
        """Loop paralelo dedicado a drenar DMs com intervalo menor."""
        interval = self._cfg.DM_POLL_INTERVAL_SEC
        log.info(f"📨 DM loop iniciado (intervalo: {interval}s)")
        while True:
            try:
                await self._drain_dm_queue()
            except Exception as exc:
                log.warning(f"_dm_loop: {exc}")
            await asyncio.sleep(interval)

    async def run_forever(self) -> None:
        interval = self._cfg.POLL_INTERVAL_MIN * 60

        log.info("=" * 60)
        log.info("Garimpeiro v5.0 iniciado")
        log.info(f"  Intervalo garimpo  : {self._cfg.POLL_INTERVAL_MIN} min")
        log.info(f"  Intervalo DM poll  : {self._cfg.DM_POLL_INTERVAL_SEC}s")
        log.info(f"  Desconto min.      : {self._cfg.MIN_DISCOUNT_PCT}%")
        log.info(f"  Deals/ciclo        : {self._cfg.DEALS_PER_CYCLE}")
        log.info(f"  DM queue           : Neon direto (sem HTTP interno)")   # [V5-2]
        log.info(f"  Vídeos             : local_video_worker.py")             # [V5-1]
        log.info(f"  Webhook            : Vercel Serverless")                 # [V5-3]
        log.info("=" * 60)

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
            log.info(f"Dormindo {self._cfg.POLL_INTERVAL_MIN}min — próximo às {nxt}")
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
        log.info("Garimpeiro encerrado (Ctrl+C)")
    except EnvironmentError as exc:
        log.critical(str(exc))
        raise SystemExit(1)
