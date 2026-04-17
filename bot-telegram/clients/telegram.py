"""
bot-telegram/clients/telegram.py — Cliente Telegram com melhorias.

[FIX-6.2] Rate limit (429) agora:
  - Lança TelegramRateLimitError para interromper o ciclo
  - Implementa retry com backoff exponencial
  - Loga claramente quando ocorre

[BP] Novo método send_price_bug_alert para alertas de bug de preço.
"""

import asyncio
import logging
from datetime import date
from typing import Optional

import httpx

from clients.shopee import SHOPEE_ERR_AUTH, SHOPEE_ERR_RATE_LIMIT
from models.deal import Deal

log = logging.getLogger("garimpeiro")


class TelegramRateLimitError(Exception):
    """Lançada quando o Telegram retorna 429. Interrompe o ciclo."""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Telegram rate limit — aguardar {retry_after}s")


class TelegramClient:
    TELEGRAM_API = "https://api.telegram.org/bot"
    _SEND_DELAY  = 2.5
    _MAX_RETRIES = 3

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

    # ── Envio de deal ─────────────────────────────────────────────────────────

    async def send_deal(self, deal: Deal, is_repost: bool = False) -> bool:
        """Envia um deal. Propaga TelegramRateLimitError se 429."""
        caption = deal.to_telegram_caption(is_repost=is_repost)
        if deal.image_url:
            ok = await self._send_photo(deal.image_url, caption, self._channel_id)
            if ok:
                await asyncio.sleep(self._SEND_DELAY)
                return True
            log.warning("Telegram: sendPhoto falhou — fallback para sendMessage")
        ok = await self._send_message(caption, self._channel_id)
        await asyncio.sleep(self._SEND_DELAY)
        return ok

    # ── [BP] Alerta de bug de preço ───────────────────────────────────────────

    async def send_price_bug_alert(self, deal: Deal) -> None:
        """
        [BP] Envia alerta separado de bug de preço para o admin.
        Caption diferenciado com urgência máxima.
        """
        discount_str = f"{deal.discount_pct}%" if deal.discount_pct else "?"
        price_str    = f"R$ {deal.price:,.2f}" if deal.price else "?"
        orig_str     = f"R$ {deal.original_price:,.2f}" if deal.original_price else "?"

        msg = (
            "🚨🐛 *ALERTA — BUG DE PREÇO DETECTADO!* 🐛🚨\n\n"
            f"*{deal.title}*\n\n"
            f"~~{orig_str}~~ → *{price_str}* (*{discount_str} OFF*)\n\n"
            f"🔗 [Link do produto]({deal.affiliate_url})\n\n"
            "⚠️ _Verificar imediatamente — pode expirar em minutos!_"
        )
        await self._send_message(msg, self._admin_chat_id)
        log.warning(f"🐛 Bug de preço alertado: {deal.title[:50]} ({discount_str} OFF)")

    # ── Mensagens administrativas ─────────────────────────────────────────────

    async def send_daily_hello(self, stats: dict) -> None:
        lines = [
            "🌅 *Garimpeiro v5.0 — Relatório Diário*", "",
            f"📅 Data: {date.today().strftime('%d/%m/%Y')}",
            f"⏱ Intervalo de ciclo: {stats.get('interval_min', '?')} min",
            f"💸 Desconto mínimo: {stats.get('min_discount', '?')}%",
            f"📦 Deals por ciclo: {stats.get('deals_per_cycle', '?')}",
            f"⭐ Rating mínimo: {stats.get('min_rating', '?')}",
            f"📊 Vendas mínimas: {stats.get('min_sold', '?')}",
            f"📨 DM poll interval: {stats.get('dm_interval_sec', '?')}s",
            f"🔄 TTL deals: {stats.get('deal_ttl_days', '?')} dias",
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
            "telegram_rate_limit": (
                "⚠️ *AVISO — TELEGRAM RATE LIMIT*\n\n"
                f"Rate limit atingido. Ciclo interrompido.\n"
                f"Retomando em: `{detail or '?'}s`\n"
                "_Próximo ciclo será retardado automaticamente._"
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

    # ── Primitivas HTTP com backoff ───────────────────────────────────────────

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

    async def _post(self, method: str, payload: dict, _attempt: int = 1) -> bool:
        """
        [FIX-6.2] Executa POST com retry e backoff exponencial.
        Lança TelegramRateLimitError em 429 para interromper o ciclo.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}/{method}", json=payload)

            # [FIX-6.2] 429 — rate limit: NÃO silencia, lança exceção
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 60)
                log.error(
                    f"🚫 [FIX-6.2] Telegram rate limit em '{method}' "
                    f"(tentativa {_attempt}) — retry_after={retry_after}s"
                )
                raise TelegramRateLimitError(retry_after=retry_after)

            if resp.status_code == 400:
                log.warning(f"Telegram 400 em {method}: {resp.text[:150]}")
                return False

            if resp.status_code in (500, 502, 503) and _attempt < self._MAX_RETRIES:
                wait = 2 ** _attempt  # backoff exponencial: 2s, 4s, 8s
                log.warning(
                    f"Telegram {resp.status_code} em '{method}' — "
                    f"retry {_attempt}/{self._MAX_RETRIES} em {wait}s"
                )
                await asyncio.sleep(wait)
                return await self._post(method, payload, _attempt + 1)

            resp.raise_for_status()
            return True

        except TelegramRateLimitError:
            raise  # propaga para interromper o ciclo

        except Exception as e:
            if _attempt < self._MAX_RETRIES:
                wait = 2 ** _attempt
                log.warning(f"Telegram: erro em '{method}' (tentativa {_attempt}) — retry em {wait}s: {e}")
                await asyncio.sleep(wait)
                return await self._post(method, payload, _attempt + 1)
            log.error(f"Telegram: erro definitivo em '{method}' após {_attempt} tentativas: {e}")
            return False
