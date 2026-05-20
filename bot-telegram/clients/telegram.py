import asyncio
import httpx
import random
import time
from models.deal import Deal
from core.logger import setup_logger
from core.exceptions import TelegramRateLimitError, TelegramNetworkError

log = setup_logger(__name__)

class TelegramClient:
    def __init__(self, token: str, chat_id: str):
        self._token = token
        self._chat_id = chat_id
        self._base_url = f"https://api.telegram.org/bot{self._token}"
        
        # --- Flood Control Architecture ---
        self._lock = asyncio.Lock()
        self._last_send_time = 0.0
        self._MIN_DELAY_BETWEEN_MESSAGES = 3.5 # Telegram pede min de ~3s por chat
        self._MAX_RETRIES = 4

    async def _wait_for_capacity(self):
        """Aplica o Rate Limiter estrito (Token Bucket simples)."""
        now = time.monotonic()
        time_since_last = now - self._last_send_time
        if time_since_last < self._MIN_DELAY_BETWEEN_MESSAGES:
            sleep_time = self._MIN_DELAY_BETWEEN_MESSAGES - time_since_last
            await asyncio.sleep(sleep_time)

    async def send_deal(self, deal: Deal, is_repost: bool = False) -> bool:
        message = self._format_message(deal, is_repost)
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }

        # Concorrência Segura: O Lock global garante que NUNCA 
        # duas ofertas disparem juntas e levem ban do Telegram.
        async with self._lock:
            await self._wait_for_capacity()
            success = await self._post_with_retry("sendMessage", payload)
            self._last_send_time = time.monotonic()
            return success

    async def _post_with_retry(self, endpoint: str, payload: dict) -> bool:
        """Exponential Backoff com Jitter Profissional."""
        url = f"{self._base_url}/{endpoint}"
        
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=payload)
                    
                    if resp.status_code == 200:
                        return True
                        
                    data = resp.json()
                    
                    if resp.status_code == 429: # Rate Limit
                        retry_after = data.get("parameters", {}).get("retry_after", 5)
                        raise TelegramRateLimitError(retry_after)
                        
                    if resp.status_code >= 500: # Network/Server Error
                        raise TelegramNetworkError(f"Telegram Internal Error: {resp.status_code}")
                        
                    # Erro fatal do cliente (ex: chat_id errado)
                    log.error(f"[TELEGRAM_FATAL] Falha não recuperável: {data}")
                    return False
                    
            except httpx.RequestError as e:
                log.warning(f"[TELEGRAM_NETWORK] Tentativa {attempt}/{self._MAX_RETRIES} falhou: {e}")
                if attempt == self._MAX_RETRIES:
                    return False
                
                # Backoff Exponencial Padrão: 2s, 4s, 8s...
                delay = 2 ** attempt
                # Jitter: Adiciona aletoriedade de ±20% para evitar "Thundering Herd"
                jitter = delay * 0.2 * random.uniform(-1, 1)
                await asyncio.sleep(delay + jitter)
                
            except TelegramRateLimitError as e:
                log.warning(f"[TELEGRAM_FLOOD] Bloqueado. Aguardando {e.retry_after}s...")
                await asyncio.sleep(e.retry_after + 1.0) # Espera o tempo exato + margem

        return False

    def _format_message(self, deal: Deal, is_repost: bool) -> str:
        header = "♻️ <b>ACHADINHO DE VOLTA!</b>\n" if is_repost else "🚨 <b>NOVO ACHADINHO!</b>\n"
        # Ajuste conforme as variáveis reais do seu model Deal
        msg = (
            f"{header}\n"
            f"📦 <b>{deal.title}</b>\n\n"
            f"💰 Por apenas: <b>R$ {deal.price:.2f}</b>\n\n"
            f"🛒 Compre aqui: <a href='{deal.affiliate_url}'>Acessar Oferta</a>"
        )
        return msg