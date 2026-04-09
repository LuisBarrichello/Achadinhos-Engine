"""
=============================================================
 Achadinhos do Momento — Bot Garimpeiro v2 (Autônomo)
 Background Worker 24/7 para Render Free Tier
=============================================================
Fluxo:
  APScheduler (a cada 30 min)
    └─► fetch_rss_deals()          — lê feeds configurados
          └─► filtra duplicatas    — via seen_ids.json
                └─► send_telegram() — sendPhoto no canal
                      └─► post_api_link() — POST /links na FastAPI

Variáveis de ambiente obrigatórias:
  TELEGRAM_BOT_TOKEN   — token do @BotFather
  TELEGRAM_CHANNEL_ID  — @seucanal ou -100xxxxxxx
  API_BASE_URL         — https://sua-api.onrender.com
  ADMIN_SECRET         — mesmo valor de x-admin-secret

Variáveis opcionais:
  POLL_INTERVAL_MIN    — intervalo entre ciclos (padrão: 30)
  MIN_DISCOUNT_PCT     — desconto mínimo para postar (padrão: 0)
  SEEN_IDS_FILE        — caminho do arquivo de dedup (padrão: ./seen_ids.json)
  RSS_FEEDS            — feeds separados por vírgula (sobrescreve padrão)
=============================================================
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import feedparser
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

# ─── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("garimpeiro")

# ─── Config ──────────────────────────────────────────────────
def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"❌ Variável de ambiente obrigatória não encontrada: {key}\n"
            f"   Defina-a no painel do Render ou no arquivo .env"
        )
    return val


BOT_TOKEN    = _require("TELEGRAM_BOT_TOKEN")
CHANNEL_ID   = _require("TELEGRAM_CHANNEL_ID")
API_BASE_URL = _require("API_BASE_URL").rstrip("/")
ADMIN_SECRET = _require("ADMIN_SECRET")

POLL_INTERVAL_MIN = int(os.getenv("POLL_INTERVAL_MIN", "30"))
MIN_DISCOUNT_PCT  = int(os.getenv("MIN_DISCOUNT_PCT", "0"))
SEEN_IDS_FILE     = Path(os.getenv("SEEN_IDS_FILE", "./seen_ids.json"))

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ─── Feeds RSS padrão ────────────────────────────────────────
# Sobrescreva via env RSS_FEEDS="url1,url2,url3"
DEFAULT_RSS_FEEDS = [
    # Pelando — feed público de ofertas
    "https://www.pelando.com.br/api/feeds/deals",
    # Promobit — feed público (remova se não quiser)
    "https://www.promobit.com.br/feed/",
]

_env_feeds = os.getenv("RSS_FEEDS", "")
RSS_FEEDS = [f.strip() for f in _env_feeds.split(",") if f.strip()] or DEFAULT_RSS_FEEDS


# ─── Persistência de dedup (seen_ids.json) ───────────────────
def load_seen_ids() -> set[str]:
    """Carrega IDs já processados do arquivo JSON."""
    if SEEN_IDS_FILE.exists():
        try:
            with open(SEEN_IDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                ids = set(data.get("seen", []))
                log.info(f"📂 Dedup carregado: {len(ids)} IDs conhecidos")
                return ids
        except (json.JSONDecodeError, KeyError) as e:
            log.warning(f"⚠️  Erro ao ler {SEEN_IDS_FILE}: {e} — iniciando do zero")
    return set()


def save_seen_ids(seen: set[str]) -> None:
    """Persiste o set de IDs no arquivo JSON."""
    try:
        with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump({"seen": list(seen), "updated_at": datetime.utcnow().isoformat()}, f, indent=2)
    except OSError as e:
        log.error(f"❌ Não foi possível salvar {SEEN_IDS_FILE}: {e}")


# ─── Estado global em memória ────────────────────────────────
seen_ids: set[str] = load_seen_ids()


# ─── Extrai URL de imagem de uma entrada RSS ─────────────────
def extract_image_url(entry: feedparser.FeedParserDict) -> Optional[str]:
    """
    Tenta extrair URL de imagem de diferentes campos do RSS:
    1. media:thumbnail / media:content  (padrão Pelando/Promobit)
    2. enclosures
    3. <img> no summary/content HTML
    """
    # 1. media:thumbnail
    media_thumbnail = getattr(entry, "media_thumbnail", None)
    if media_thumbnail and isinstance(media_thumbnail, list):
        url = media_thumbnail[0].get("url")
        if url:
            return url

    # 2. media:content
    media_content = getattr(entry, "media_content", None)
    if media_content and isinstance(media_content, list):
        for mc in media_content:
            if mc.get("medium") == "image" or mc.get("type", "").startswith("image"):
                url = mc.get("url")
                if url:
                    return url

    # 3. enclosures (podcasts e feeds genéricos)
    for enc in getattr(entry, "enclosures", []):
        if enc.get("type", "").startswith("image"):
            return enc.get("href") or enc.get("url")

    # 4. Primeiro <img> do HTML do summary
    summary = getattr(entry, "summary", "") or ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, re.IGNORECASE)
    if match:
        return match.group(1)

    return None


# ─── Extrai desconto da entrada (se disponível) ──────────────
def extract_discount(entry: feedparser.FeedParserDict) -> int:
    """Tenta extrair percentual de desconto do título ou summary."""
    text = f"{getattr(entry, 'title', '')} {getattr(entry, 'summary', '')}"
    match = re.search(r'(\d{1,3})\s*%\s*(?:off|de\s*desconto|desconto)', text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


# ─── Deriva um ID único para a entrada ───────────────────────
def entry_id(entry: feedparser.FeedParserDict) -> str:
    """Usa o ID do feed, ou a URL do link, como chave de dedup."""
    return getattr(entry, "id", None) or getattr(entry, "link", "") or ""


# ─── Busca e filtra ofertas dos feeds RSS ────────────────────
async def fetch_rss_deals() -> list[dict]:
    """
    Percorre todos os RSS_FEEDS e retorna lista de dicts com:
      { id, title, url, image_url, discount }
    Filtra os que já foram vistos (seen_ids) e os abaixo do desconto mínimo.
    """
    new_deals: list[dict] = []

    for feed_url in RSS_FEEDS:
        log.info(f"🔎 Verificando feed: {feed_url}")
        try:
            # feedparser é síncrono — rodamos em executor para não bloquear o loop
            loop = asyncio.get_running_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, feed_url)

            entries = feed.get("entries", [])
            log.info(f"   └─ {len(entries)} entradas encontradas")

            for entry in entries:
                eid = entry_id(entry)
                if not eid:
                    continue  # sem ID não há como deduplicar com segurança

                if eid in seen_ids:
                    continue  # já processado

                title = getattr(entry, "title", "Oferta sem título").strip()
                url   = getattr(entry, "link", "").strip()
                if not url:
                    continue

                discount = extract_discount(entry)
                if MIN_DISCOUNT_PCT > 0 and discount < MIN_DISCOUNT_PCT:
                    log.debug(f"   ↷ Desconto insuficiente ({discount}%): {title[:50]}")
                    continue

                image_url = extract_image_url(entry)

                new_deals.append({
                    "id":        eid,
                    "title":     title,
                    "url":       url,
                    "image_url": image_url,
                    "discount":  discount,
                })

        except Exception as e:
            log.error(f"❌ Erro ao processar feed {feed_url}: {e}")

    log.info(f"✅ {len(new_deals)} oferta(s) nova(s) encontrada(s)")
    return new_deals


# ─── Formata mensagem para o Telegram ────────────────────────
def format_telegram_message(deal: dict) -> str:
    title    = deal["title"]
    url      = deal["url"]
    discount = deal.get("discount", 0)
    discount_str = f"🏷️ *{discount}% OFF*\n" if discount > 0 else ""

    return (
        f"🛍️ *{title}*\n\n"
        f"{discount_str}"
        f"👉 [Ver oferta completa]({url})\n\n"
        f"_Achadinhos do Momento_ 🔥"
    )


# ─── Envia mensagem ao Telegram ──────────────────────────────
async def send_telegram(client: httpx.AsyncClient, deal: dict) -> bool:
    """
    Envia sendPhoto se houver image_url, senão sendMessage.
    Retorna True em sucesso, False em falha.
    """
    text = format_telegram_message(deal)

    try:
        if deal.get("image_url"):
            payload = {
                "chat_id":    CHANNEL_ID,
                "photo":      deal["image_url"],
                "caption":    text,
                "parse_mode": "Markdown",
            }
            resp = await client.post(f"{TG_API}/sendPhoto", json=payload, timeout=15)
        else:
            payload = {
                "chat_id":                  CHANNEL_ID,
                "text":                     text,
                "parse_mode":               "Markdown",
                "disable_web_page_preview": False,
            }
            resp = await client.post(f"{TG_API}/sendMessage", json=payload, timeout=15)

        if resp.status_code == 200:
            msg_id = resp.json().get("result", {}).get("message_id", "?")
            log.info(f"   📤 Telegram OK → msg #{msg_id}")
            return True

        log.error(f"   ❌ Telegram erro {resp.status_code}: {resp.text[:200]}")
        return False

    except httpx.HTTPError as e:
        log.error(f"   ❌ Telegram HTTPError: {e}")
        return False


# ─── Cadastra oferta na API FastAPI ──────────────────────────
async def post_api_link(client: httpx.AsyncClient, deal: dict) -> bool:
    """
    POST /links — cadastra a oferta no banco da API.
    Retorna True em sucesso, False em falha.
    """
    payload = {
        "title":       deal["title"],
        "url":         deal["url"],
        "emoji":       "🛍️",
        "badge":       "NOVO",
        "badge_color": "#e11d48",
        "order":       10,
        "image_url":   deal.get("image_url"),
    }
    headers = {"x-admin-secret": ADMIN_SECRET}

    try:
        resp = await client.post(
            f"{API_BASE_URL}/links",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            link_id = resp.json().get("id", "?")
            log.info(f"   🗄️  API OK → link #{link_id} cadastrado")
            return True

        log.error(f"   ❌ API erro {resp.status_code}: {resp.text[:200]}")
        return False

    except httpx.HTTPError as e:
        log.error(f"   ❌ API HTTPError: {e}")
        return False


# ─── Ciclo principal de garimpo ──────────────────────────────
async def garimpar_ciclo() -> None:
    """
    Executado pelo APScheduler a cada POLL_INTERVAL_MIN minutos.
    1. Busca ofertas novas nos feeds
    2. Para cada oferta: envia Telegram → cadastra API → marca como vista
    """
    global seen_ids
    log.info(f"{'─'*50}")
    log.info(f"🔄 Iniciando ciclo de garimpo — {datetime.now().strftime('%H:%M:%S')}")

    deals = await fetch_rss_deals()
    if not deals:
        log.info("💤 Nenhuma oferta nova. Próximo ciclo em %d min.", POLL_INTERVAL_MIN)
        return

    async with httpx.AsyncClient() as client:
        posted = 0
        for deal in deals:
            log.info(f"📦 Processando: {deal['title'][:60]}…")

            # 1. Envia para o Telegram
            tg_ok = await send_telegram(client, deal)
            if not tg_ok:
                log.warning(f"   ⚠️  Telegram falhou — oferta NÃO cadastrada na API para evitar inconsistência")
                # Marca como vista assim mesmo para não retentar infinitamente
                seen_ids.add(deal["id"])
                continue

            # Pequena pausa para respeitar rate limit do Telegram (30 msg/s)
            await asyncio.sleep(1.5)

            # 2. Cadastra na API FastAPI
            api_ok = await post_api_link(client, deal)
            if not api_ok:
                log.warning(f"   ⚠️  API falhou — oferta foi postada no Telegram mas não na vitrine")

            # 3. Marca como vista em memória e persiste
            seen_ids.add(deal["id"])
            posted += 1

    # Persiste o arquivo de dedup ao final do ciclo
    save_seen_ids(seen_ids)
    log.info(f"✅ Ciclo concluído — {posted}/{len(deals)} oferta(s) postada(s)")
    log.info(f"   Próximo ciclo em {POLL_INTERVAL_MIN} minuto(s)")


# ─── Entry point ─────────────────────────────────────────────
async def main() -> None:
    log.info("=" * 50)
    log.info("🚀 Garimpeiro iniciado")
    log.info(f"   Canal: {CHANNEL_ID}")
    log.info(f"   API:   {API_BASE_URL}")
    log.info(f"   Feeds: {len(RSS_FEEDS)} configurado(s)")
    log.info(f"   Ciclo: a cada {POLL_INTERVAL_MIN} minuto(s)")
    log.info(f"   Dedup: {SEEN_IDS_FILE}")
    if MIN_DISCOUNT_PCT > 0:
        log.info(f"   Filtro: desconto mínimo de {MIN_DISCOUNT_PCT}%")
    log.info("=" * 50)

    # Roda um ciclo imediatamente na inicialização
    await garimpar_ciclo()

    # Agenda ciclos seguintes com APScheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        garimpar_ciclo,
        trigger="interval",
        minutes=POLL_INTERVAL_MIN,
        id="garimpar",
        max_instances=1,       # evita sobreposição de ciclos lentos
        misfire_grace_time=60, # tolera 60s de atraso sem pular o job
    )
    scheduler.start()
    log.info(f"⏰ Scheduler ativo — próxima execução em {POLL_INTERVAL_MIN} min")

    # Mantém o processo vivo para sempre (Render Background Worker)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        log.info("🛑 Garimpeiro encerrado pelo operador")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
