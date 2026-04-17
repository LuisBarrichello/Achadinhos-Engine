"""
bot-telegram/core/config.py — Configuração central do Garimpeiro.

Novos parâmetros:
  [QF]  MIN_RATING, MIN_SOLD          — filtros de qualidade do produto
  [RP]  REPOST_DAYS, REPOST_MAX       — dias e quantidade de reposts
  [TTL] DEAL_TTL_DAYS                 — TTL do processed_deals.json
  [BP]  PRICE_BUG_THRESHOLD           — limiar de bug de preço
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("garimpeiro")


class Config:
    # ── Shopee ────────────────────────────────────────────────────────────────
    SHOPEE_APP_ID     : str = os.getenv("SHOPEE_APP_ID",     "")
    SHOPEE_APP_SECRET : str = os.getenv("SHOPEE_APP_SECRET", "")
    SHOPEE_SUB_ID     : str = os.getenv("SHOPEE_SUB_ID",     "achadinhos")

    # ── Telegram ──────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN     : str = os.getenv("TELEGRAM_BOT_TOKEN",     "")
    TELEGRAM_CHANNEL_ID    : str = os.getenv("TELEGRAM_CHANNEL_ID",    "")
    ADMIN_TELEGRAM_CHAT_ID : str = os.getenv(
        "ADMIN_TELEGRAM_CHAT_ID",
        os.getenv("TELEGRAM_CHANNEL_ID", "")
    )

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL      : str = os.getenv("DATABASE_URL", "")
    API_BASE_URL      : str = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
    ADMIN_SECRET      : str = os.getenv("ADMIN_SECRET", "")
    PAGE_ACCESS_TOKEN : str = os.getenv("PAGE_ACCESS_TOKEN", "")

    # ── Ciclos ────────────────────────────────────────────────────────────────
    POLL_INTERVAL_MIN    : int   = int(os.getenv("POLL_INTERVAL_MIN",    "30"))
    DM_POLL_INTERVAL_SEC : int   = int(os.getenv("DM_POLL_INTERVAL_SEC", "60"))
    MIN_DISCOUNT_PCT     : int   = int(os.getenv("MIN_DISCOUNT_PCT",     "20"))
    DEALS_PER_CYCLE      : int   = int(os.getenv("DEALS_PER_CYCLE",      "5"))
    HTTP_TIMEOUT         : float = float(os.getenv("HTTP_TIMEOUT",       "20.0"))

    PROCESSED_DEALS_PATH : Path = Path(
        os.getenv("PROCESSED_DEALS_PATH", "processed_deals.json")
    )

    # ── [QF] Filtros de qualidade do produto ──────────────────────────────────
    MIN_RATING : float = float(os.getenv("MIN_RATING", "4.5"))
    MIN_SOLD   : int   = int(os.getenv("MIN_SOLD",   "1000"))

    # ── [RP] Repost automático ────────────────────────────────────────────────
    # Dias da semana para repost: 4=sexta, 5=sábado, 6=domingo (weekday() Python)
    REPOST_DAYS : list[int] = [
        int(d) for d in os.getenv("REPOST_DAYS", "4,5,6").split(",")
    ]
    REPOST_MAX      : int = int(os.getenv("REPOST_MAX",      "3"))   # reposts por ciclo
    REPOST_MIN_CLICKS: int = int(os.getenv("REPOST_MIN_CLICKS", "5")) # cliques mínimos

    # ── [TTL] TTL do processed_deals ─────────────────────────────────────────
    DEAL_TTL_DAYS : int = int(os.getenv("DEAL_TTL_DAYS", "14"))

    # ── [BP] Detecção de bug de preço ─────────────────────────────────────────
    PRICE_BUG_THRESHOLD : int = int(os.getenv("PRICE_BUG_THRESHOLD", "60"))

    DM_MAX_RETRIES : int = int(os.getenv("DM_MAX_RETRIES", "3"))

    @classmethod
    def validate(cls) -> None:
        must_have = {
            "SHOPEE_APP_ID"      : cls.SHOPEE_APP_ID,
            "SHOPEE_APP_SECRET"  : cls.SHOPEE_APP_SECRET,
            "TELEGRAM_BOT_TOKEN" : cls.TELEGRAM_BOT_TOKEN,
            "TELEGRAM_CHANNEL_ID": cls.TELEGRAM_CHANNEL_ID,
            "ADMIN_SECRET"       : cls.ADMIN_SECRET,
            "DATABASE_URL"       : cls.DATABASE_URL,
        }
        missing = [k for k, v in must_have.items() if not v]
        if missing:
            raise EnvironmentError(
                "Variáveis de ambiente obrigatórias não definidas:\n"
                + "\n".join(f"  · {k}" for k in missing)
            )
        if not cls.PAGE_ACCESS_TOKEN:
            log.warning("⚠️  PAGE_ACCESS_TOKEN ausente — DMs não serão enviadas.")
        log.info(
            f"✅ Config validada | rating≥{cls.MIN_RATING} "
            f"sold≥{cls.MIN_SOLD} TTL={cls.DEAL_TTL_DAYS}d "
            f"bug_threshold={cls.PRICE_BUG_THRESHOLD}%"
        )
