import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("garimpeiro")


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
