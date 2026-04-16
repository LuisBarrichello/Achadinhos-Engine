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
from clients.telegram import TelegramClient
from clients.vitrine import VitrineAPI
from core.config import Config
from database.dm_processor import DMProcessor
from models.deal import Deal
from storage.deal_store import DealStore

log = logging.getLogger("garimpeiro")


class Garimpeiro:

    def __init__(self, cfg: type[Config]) -> None:
        self._cfg   = cfg
        self._store = DealStore(cfg.PROCESSED_DEALS_PATH)

        self._shopee = ShopeeAPI(
            cfg.SHOPEE_APP_ID,
            cfg.SHOPEE_APP_SECRET,
            cfg.SHOPEE_SUB_ID,
            cfg.HTTP_TIMEOUT,
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

        # [V5-2] Acesso direto ao Neon — sem HTTP interno
        self._dm_processor = DMProcessor(
            database_url      = cfg.DATABASE_URL,
            page_access_token = cfg.PAGE_ACCESS_TOKEN,
            timeout           = cfg.HTTP_TIMEOUT,
        )

        self._last_hello_date : Optional[date] = None

    # ── Helpers internos ──────────────────────────────────────────────────────

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

    # ── Ciclo principal ───────────────────────────────────────────────────────

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

        new_items = [
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

            # [V5-1] Sem _run_video_pipeline — vídeos gerados localmente
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
        """Loop paralelo dedicado a drenar DMs com intervalo menor."""
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
