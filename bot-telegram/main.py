import os
import asyncio
import logging
from worker.garimpeiro import Garimpeiro
from database.dm_processor import DMProcessor
from storage.deal_store import DealStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


async def main():
    db_url = os.getenv("DATABASE_URL")
    pat = os.getenv("PAGE_ACCESS_TOKEN")

    if not db_url or not pat:
        log.error("Variáveis de ambiente ausentes. Abortando.")
        return

    # 1. Inicializa o estado persistente do BD
    store = DealStore(db_url=db_url)

    # 2. Inicializa os Workers
    dm_processor = DMProcessor(db_url=db_url, pat=pat)
    garimpeiro = (Garimpeiro(store=store))  # Assumindo injeção do seu worker atual

    log.info("Iniciando Achadinhos-Engine (Workers)...")

    try:
        # Executa ambos os loops concorrentemente na mesma thread do asyncio
        await asyncio.gather(
            garimpeiro.run_cycle(),  # Seu loop infinito do Telegram/Shopee
            dm_processor.start_loop()  # Nosso novo loop blindado de DMs
        )
    except asyncio.CancelledError:
        log.warning("Sinal de desligamento recebido. Finalizando workers graciosamente...")
    except Exception as e:
        log.error(f"Fatal erro no orquestrador: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Processo encerrado pelo usuário.")