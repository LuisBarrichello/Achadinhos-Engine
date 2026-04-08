"""
=============================================================
 Achadinhos do Momento — Bot Telegram "Garimpeiro"
 Envia ofertas do backend para um canal/grupo do Telegram
=============================================================
Uso:
  python garimpeiro.py             # envia todos os links ativos
  python garimpeiro.py --dry-run   # apenas exibe, não envia
=============================================================
"""

import argparse
import asyncio
import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("garimpeiro")

# ── Config ────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]   # obrigatório
CHANNEL_ID   = os.environ["TELEGRAM_CHANNEL_ID"]  # ex: @achadinhosdomomento ou -100xxxx
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ── Buscar links do backend ───────────────────────────────────
async def fetch_links(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(f"{API_BASE_URL}/links")
    resp.raise_for_status()
    return resp.json()


# ── Formatar mensagem de oferta ───────────────────────────────
def format_offer(link: dict) -> str:
    emoji      = link.get("emoji", "🛍️")
    title      = link.get("title", "Sem título")
    badge      = link.get("badge", "")
    url        = link.get("url", "")
    badge_str  = f"[{badge}] " if badge else ""

    return (
        f"{emoji} *{badge_str}{title}*\n\n"
        f"👉 [Ver oferta]({url})\n\n"
        f"_Achadinhos do Momento_ 🛒"
    )


# ── Enviar mensagem ao Telegram ───────────────────────────────
async def send_message(client: httpx.AsyncClient, text: str, image_url: str | None = None) -> dict:
    if image_url:
        payload = {
            "chat_id": CHANNEL_ID,
            "photo":   image_url,
            "caption": text,
            "parse_mode": "Markdown",
        }
        resp = await client.post(f"{TG_API}/sendPhoto", json=payload)
    else:
        payload = {
            "chat_id": CHANNEL_ID,
            "text":    text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
        resp = await client.post(f"{TG_API}/sendMessage", json=payload)

    resp.raise_for_status()
    return resp.json()


# ── Main ──────────────────────────────────────────────────────
async def main(dry_run: bool = False):
    async with httpx.AsyncClient(timeout=20) as client:
        log.info(f"🔍 Buscando links em {API_BASE_URL}...")
        links = await fetch_links(client)
        log.info(f"✅ {len(links)} links encontrados")

        for link in links:
            text      = format_offer(link)
            image_url = link.get("image_url")

            if dry_run:
                log.info(f"\n{'─'*40}\n{text}\nImagem: {image_url or '(nenhuma)'}\n")
                continue

            try:
                result = await send_message(client, text, image_url)
                msg_id = result.get("result", {}).get("message_id", "?")
                log.info(f"📤 Enviado: '{link['title']}' → msg #{msg_id}")
                await asyncio.sleep(1.5)  # respeita rate limit do Telegram
            except httpx.HTTPStatusError as e:
                log.error(f"❌ Falha ao enviar '{link['title']}': {e.response.text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Garimpeiro — envia ofertas ao Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Exibe mensagens sem enviar")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
