import asyncio
import time
from typing import List
from models.deal import Deal
from clients.telegram import TelegramClient
from clients.shopee import ShopeeClient # Seu mock atual
from storage.deal_store import DealStore
from services.scoring import ScoringService
from core.logger import setup_logger

log = setup_logger(__name__)

class GarimpeiroWorker:
    def __init__(self, store: DealStore, telegram: TelegramClient, shopee: ShopeeClient):
        self._store = store
        self._telegram = telegram
        self._shopee = shopee
        
        self._POLL_INTERVAL = 30 * 60 # 30 Minutos (Garimpo normal)
        self._running = False

    async def run_cycle(self):
        """Lifecycle seguro com Graceful Shutdown"""
        self._running = True
        log.info("Garimpeiro Worker [Iniciado]")
        
        while self._running:
            try:
                cycle_start = time.time()
                
                # 1. Garimpo Real
                await self._run_fetch_cycle()
                
                # 2. Repost de Ofertas Antigas
                await self._run_repost_cycle()
                
                elapsed = time.time() - cycle_start
                sleep_time = max(0, self._POLL_INTERVAL - elapsed)
                
                log.info(f"Ciclo finalizado. Dormindo por {sleep_time/60:.1f} minutos.")
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                log.warning("Sinal de cancelamento recebido. Finalizando Garimpeiro.")
                self._running = False
                break
            except Exception as e:
                log.error(f"[CRITICAL] Erro não tratado no Worker: {e}", exc_info=True)
                # Backpressure: se o banco ou API estiverem fora, pausa 1 minuto e retenta
                await asyncio.sleep(60)

    async def _run_fetch_cycle(self):
        log.info("[FETCH] Buscando novas ofertas da Shopee...")
        deals = await self._shopee.fetch_top_products(limit=15)
        
        # Filtra ofertas já postadas
        novas_ofertas = [d for d in deals if not self._store.is_processed(d.item_id)]
        
        if not novas_ofertas:
            log.info("[FETCH] Nenhuma oferta nova encontrada.")
            return

        # Priorização: Ofertas com maior desconto vão primeiro
        ranked_deals = ScoringService.rank_deals(novas_ofertas)
        
        for deal in ranked_deals:
            if not self._running: break # Cancela no meio se necessário
            
            log.info(f"[TELEGRAM] Enviando (Score: {deal.score}): {deal.title[:30]}...")
            success = await self._telegram.send_deal(deal, is_repost=False)
            
            if success:
                # Salva o status apenas se o Telegram confirmar envio com sucesso
                self._store.save_deal(deal)
            else:
                log.warning(f"Falha ao enviar {deal.item_id}. Tentará novamente no próximo ciclo.")

    async def _run_repost_cycle(self):
        """O Repost Real e Estratégico."""
        log.info("[REPOST] Buscando ofertas antigas para reciclar...")
        # Pega até 3 ofertas de 7 dias atrás
        candidates = self._store.get_candidates_for_repost(days_old=7, limit=3)
        
        if not candidates:
            log.info("[REPOST] Sem candidatos no momento.")
            return

        for deal in candidates:
            if not self._running: break

            log.info(f"[REPOST] Re-enviando oferta antiga: {deal.title[:30]}...")
            success = await self._telegram.send_deal(deal, is_repost=True)
            
            if success:
                # Atualiza a data de processamento para ela ir pro final da fila (+7 dias)
                self._store.save_deal(deal)