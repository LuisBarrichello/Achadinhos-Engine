import os

from dotenv import load_dotenv

load_dotenv()


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
