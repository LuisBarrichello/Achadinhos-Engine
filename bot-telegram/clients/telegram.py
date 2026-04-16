import asyncio
import logging
from datetime import date
from typing import Optional

import httpx

from clients.shopee import SHOPEE_ERR_AUTH, SHOPEE_ERR_RATE_LIMIT
from models.deal import Deal

log = logging.getLogger("garimpeiro")


class TelegramClient:
    TELEGRAM_API = "https://api.telegram.org/bot"
    _SEND_DELAY  = 2.5

    def __init__(
        self,
        token         : str,
        channel_id    : str,
        admin_chat_id : str,
        timeout       : float,
    ) -> None:
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

    async def _send_photo(self, photo_url: str, caption: str, chat_id: str) -> bool:
        return await self._post("sendPhoto", {
            "chat_id"   : chat_id,
            "photo"     : photo_url,
            "caption"   : caption,
            "parse_mode": "Markdown",
        })

    async def _send_message(self, text: str, chat_id: str) -> bool:
        return await self._post("sendMessage", {
            "chat_id"                 : chat_id,
            "text"                    : text,
            "parse_mode"              : "Markdown",
            "disable_web_page_preview": False,
        })

    async def _post(self, method: str, payload: dict) -> bool:
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
