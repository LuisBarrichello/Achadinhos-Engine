import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

load_dotenv()


class Settings(BaseSettings):
    ENVIRONMENT: str = "production"  # dev, staging, prod

    # Database
    DATABASE_URL: str

    # Redis (Para Rate Limit e Cache)
    REDIS_URL: str = "redis://localhost:6379/0"

    # Meta / Instagram
    WEBHOOK_VERIFY_TOKEN: str
    META_APP_SECRET: str
    PAGE_ACCESS_TOKEN: str

    # Segurança
    ADMIN_SECRET: str
    FRONTEND_ORIGIN: str = "http://localhost:3000"

    # Observabilidade
    SENTRY_DSN: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

# Falha instantaneamente se as vars obrigatórias não estiverem no .env
settings = Settings()

def _require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"Variável de ambiente obrigatória não definida: {key}\n"
            f"Consulte o arquivo .env.example para referência."
        )
    return val


DATABASE_URL         = _require_env("DATABASE_URL")
WEBHOOK_VERIFY_TOKEN = _require_env("WEBHOOK_VERIFY_TOKEN")
META_APP_SECRET      = os.getenv("META_APP_SECRET", "")
PAGE_ACCESS_TOKEN    = os.getenv("PAGE_ACCESS_TOKEN", "")
ADMIN_SECRET         = _require_env("ADMIN_SECRET")
FRONTEND_ORIGIN      = os.getenv("FRONTEND_ORIGIN", "http://localhost:5500")
