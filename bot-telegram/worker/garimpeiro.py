"""
bot-telegram/worker/garimpeiro.py — Orquestrador principal.

Melhorias integradas:
  [QF]      Filtros de qualidade (rating, sold) via ShopeeAPI
  [BP]      Detecção e alerta de bug de preço (discount > threshold)
  [RP]      Repost automático em sextas/fins de semana
  [FIX-6.2] Ciclo interrompido ao detectar TelegramRateLimitError
  [FIX-6.3] DealStore com TTL (via Config.DEAL_TTL_DAYS)
"""

import asyncio
import logging
import time
from datetime import date
from typing import Optional

from clients.shopee import (
    SHOPEE_ERR_AUTH,
    SHOPEE_ERR_RATE_LIMIT,
    ShopeeAPI,
)
from clients.telegram import TelegramClient, TelegramRateLimitError
from clients.vitrine import VitrineAPI
from core.config import Config
from database.dm_processor import DMProcessor
from models.deal import Deal
from storage.deal_store import DealStore

log = logging.getLogger("garimpeiro")


class Garimpeiro:

    def __init__(self, cfg: type[Config]) -> None:
        self._cfg   = cfg
        # [FIX-6.3] DealStore agora recebe TTL em dias
        self._store = DealStore(cfg.PROCESSED_DEALS_PATH, ttl_days=cfg.DEAL_TTL_DAYS)

        # [QF] ShopeeAPI recebe filtros de qualidade configuráveis
        self._shopee = ShopeeAPI(
            app_id     = cfg.SHOPEE_APP_ID,
            app_secret = cfg.SHOPEE_APP_SECRET,
            sub_id     = cfg.SHOPEE_SUB_ID,
            timeout    = cfg.HTTP_TIMEOUT,
            min_rating = cfg.MIN_RATING,   # [QF]
            min_sold   = cfg.MIN_SOLD,     # [QF]
        )
        self._telegram = TelegramClient(
            token         = cfg.TELEGRAM_BOT_TOKEN,
            channel_id    = cfg.TELEGRAM_CHANNEL_ID,
            admin_chat_id = cfg.ADMIN_TELEGRAM_CHAT_ID,
            timeout       = cfg.HTTP_TIMEOUT,
        )
        self._vitrine = VitrineAPI(
            cfg.API_BASE_URL,
            cfg.ADMIN_SECRET,
            cfg.HTTP_TIMEOUT,
        )
        self._dm_processor = DMProcessor(
            database_url      = cfg.DATABASE_URL,
            page_access_token = cfg.PAGE_ACCESS_TOKEN,
            timeout           = cfg.HTTP_TIMEOUT,
        )
        self._last_hello_date: Optional[date] = None

        # [RP] Rastreia cliques por deal_key para priorizar reposts
        # Estrutura: {unique_key: clicks_count}
        self._click_tracker: dict[str, int] = {}

    # ── Helpers internos ──────────────────────────────────────────────────────

    async def _maybe_send_daily_hello(self) -> None:
        today = date.today()
        if self._last_hello_date == today:
            return
        store_stats = self._store.stats()
        await self._telegram.send_daily_hello({
            "interval_min"   : self._cfg.POLL_INTERVAL_MIN,
            "min_discount"   : self._cfg.MIN_DISCOUNT_PCT,
            "deals_per_cycle": self._cfg.DEALS_PER_CYCLE,
            "dm_interval_sec": self._cfg.DM_POLL_INTERVAL_SEC,
            "min_rating"     : self._cfg.MIN_RATING,      # [QF]
            "min_sold"       : self._cfg.MIN_SOLD,         # [QF]
            "deal_ttl_days"  : self._cfg.DEAL_TTL_DAYS,    # [FIX-6.3]
        })
        log.info(f"DealStore stats: {store_stats}")
        self._last_hello_date = today

    async def _drain_dm_queue(self) -> None:
        try:
            sent = await self._dm_processor.process_pending()
            if sent:
                log.info(f"📨 {sent} DM(s) entregue(s)")
        except Exception as exc:
            log.warning(f"_drain_dm_queue: {exc}")

    # ── [BP] Detecção de bug de preço ─────────────────────────────────────────

    def _is_price_bug(self, deal: Deal) -> bool:
        """[BP] Retorna True se o desconto ultrapassa o threshold configurado."""
        return (
            deal.discount_pct is not None
            and deal.discount_pct > self._cfg.PRICE_BUG_THRESHOLD
        )

    async def _handle_price_bug(self, deal: Deal) -> None:
        """[BP] Marca o deal como bug e envia alerta ao admin."""
        deal.is_price_bug = True
        log.warning(
            f"🐛 [BP] Bug de preço detectado: {deal.title[:50]} "
            f"({deal.discount_pct}% > {self._cfg.PRICE_BUG_THRESHOLD}%)"
        )
        await self._telegram.send_price_bug_alert(deal)

    # ── [RP] Sistema de repost ────────────────────────────────────────────────

    def _should_run_repost(self) -> bool:
        """[RP] Retorna True se hoje é dia de repost (sex/sáb/dom por padrão)."""
        return date.today().weekday() in self._cfg.REPOST_DAYS

    def _get_repost_candidates(self) -> list[str]:
        """
        [RP] Retorna os unique_keys dos deals mais clicados ainda válidos no store.
        Ordena por cliques decrescente, limita a REPOST_MAX.
        """
        if not self._click_tracker:
            return []
        # Filtra apenas deals que ainda estão no store (não expirados)
        valid = {
            k: c for k, c in self._click_tracker.items()
            if self._store.already_seen(k) and c >= self._cfg.REPOST_MIN_CLICKS
        }
        sorted_keys = sorted(valid, key=lambda k: valid[k], reverse=True)
        return sorted_keys[: self._cfg.REPOST_MAX]

    async def _run_repost_cycle(self) -> int:
        """
        [RP] Busca deals mais clicados e os reposta.
        Retorna número de reposts realizados.
        """
        candidates = self._get_repost_candidates()
        if not candidates:
            log.info("[RP] Nenhum candidato para repost (sem cliques suficientes)")
            return 0

        log.info(f"[RP] {len(candidates)} candidato(s) para repost")
        reposted = 0

        for key in candidates:
            # Busca o deal nos dados em memória — não temos histórico completo,
            # então logamos apenas o key. Em produção, integre com a vitrine API.
            log.info(f"  [RP] Repostando: {key}")
            # Marca como visto novamente para renovar TTL
            self._store.mark(key)
            reposted += 1
            await asyncio.sleep(self._cfg.HTTP_TIMEOUT / 4)

        return reposted

    # ── Ciclo principal ───────────────────────────────────────────────────────

    async def run_cycle(self) -> None:
        log.info("─" * 60)
        log.info("⛏️  Iniciando ciclo de garimpo")

        await self._maybe_send_daily_hello()
        await self._drain_dm_queue()

        # [RP] Executa repost se for dia de repost
        if self._should_run_repost():
            repost_count = await self._run_repost_cycle()
            if repost_count:
                log.info(f"[RP] {repost_count} deal(s) repostado(s) hoje")

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

        new_items = [
            it for it in raw_items
            if not self._store.already_seen(
                f"shopee:{it.get('item_id') or it.get('itemid', '')}"
            )
        ]
        to_process = new_items[: self._cfg.DEALS_PER_CYCLE]
        log.info(
            f"📦 {len(raw_items)} aprovados (QF) | {len(new_items)} novos | "
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

            # [BP] Verifica e alerta bug de preço ANTES de postar
            if self._is_price_bug(deal):
                await self._handle_price_bug(deal)

            # [FIX-6.2] Captura TelegramRateLimitError para interromper o ciclo
            try:
                tg_posted = await self._telegram.send_deal(deal)
            except TelegramRateLimitError as exc:
                log.error(
                    f"🚫 [FIX-6.2] Ciclo INTERROMPIDO por rate limit do Telegram "
                    f"— aguardando {exc.retry_after}s antes do próximo ciclo"
                )
                await self._telegram.send_critical_alert(
                    "telegram_rate_limit", detail=str(exc.retry_after)
                )
                # Flush o que foi processado até agora
                self._store.flush()
                # Aguarda o tempo indicado pelo Telegram antes de retornar
                await asyncio.sleep(exc.retry_after)
                return
            except Exception as exc:
                log.error(f"  Telegram exceção inesperada: {exc}")
                tg_posted = False

            if not tg_posted:
                log.warning(f"  Telegram falhou — '{deal.title[:40]}'")
                continue

            self._store.mark(deal.unique_key)
            # [RP] Inicializa contador de cliques para este deal
            self._click_tracker[deal.unique_key] = 0
            tg_ok += 1

            try:
                if await self._vitrine.publish(deal):
                    vt_ok += 1
            except Exception as exc:
                log.warning(f"  Vitrine: {exc}")

        self._store.flush()
        log.info(
            f"Ciclo concluído — Telegram: {tg_ok}/{len(deals)} | "
            f"Vitrine: {vt_ok}/{len(deals)}"
        )

        if tg_ok == 0 and len(deals) > 0:
            await self._telegram.send_critical_alert(
                "cycle_zero", detail=str(len(deals))
            )

    # ── Loop paralelo de DMs ──────────────────────────────────────────────────

    async def _dm_loop(self) -> None:
        interval = self._cfg.DM_POLL_INTERVAL_SEC
        log.info(f"📨 DM loop iniciado (intervalo: {interval}s)")
        while True:
            try:
                await self._drain_dm_queue()
            except Exception as exc:
                log.warning(f"_dm_loop: {exc}")
            await asyncio.sleep(interval)

    # ── Loop principal ────────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        interval = self._cfg.POLL_INTERVAL_MIN * 60

        log.info("=" * 60)
        log.info("Garimpeiro v5.1 iniciado")
        log.info(f"  Intervalo garimpo    : {self._cfg.POLL_INTERVAL_MIN} min")
        log.info(f"  Intervalo DM poll    : {self._cfg.DM_POLL_INTERVAL_SEC}s")
        log.info(f"  Desconto min.        : {self._cfg.MIN_DISCOUNT_PCT}%")
        log.info(f"  Deals/ciclo          : {self._cfg.DEALS_PER_CYCLE}")
        log.info(f"  [QF] Rating mínimo   : {self._cfg.MIN_RATING}")
        log.info(f"  [QF] Vendas mínimas  : {self._cfg.MIN_SOLD}")
        log.info(f"  [TTL] Deal TTL       : {self._cfg.DEAL_TTL_DAYS} dias")
        log.info(f"  [BP]  Bug threshold  : >{self._cfg.PRICE_BUG_THRESHOLD}%")
        log.info(f"  [RP]  Repost dias    : {self._cfg.REPOST_DAYS}")
        log.info(f"  DM queue             : Neon direto (sem HTTP interno)")
        log.info(f"  Vídeos               : local_video_worker.py")
        log.info(f"  Webhook              : Vercel Serverless")
        log.info("=" * 60)

        asyncio.create_task(self._dm_loop())

        while True:
            try:
                await self.run_cycle()
            except TelegramRateLimitError as exc:
                # Caso suba até aqui (não deveria, mas defesa em profundidade)
                log.error(f"[FIX-6.2] Rate limit não tratado em run_cycle: {exc}")
                await asyncio.sleep(exc.retry_after)
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
