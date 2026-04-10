"""
=============================================================
 Achadinhos do Momento — Bot Telegram "Garimpeiro"
 Stack  : python-telegram-bot v21 + APScheduler + SQLite
 Função : Garimpa ofertas de feeds RSS/APIs públicas,
          injeta ID de afiliado e posta no canal do Telegram.
=============================================================
Fluxo:
  1. Scheduler dispara a cada N minutos
  2. Garimpa ofertas de múltiplas fontes (RSS + Pelando)
  3. Normaliza e filtra por desconto mínimo
  4. Injeta link de afiliado via Regex
  5. Formata mensagem com emoji e posta no canal
  6. Registra no banco para não repostar duplicatas
=============================================================
"""

import asyncio
import hashlib
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

import feedparser
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ─── Logging ────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("garimpeiro")

# ─── Config (variáveis de ambiente) ─────────────────────────
BOT_TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID        = os.getenv("TELEGRAM_CHANNEL_ID", "@achadinhosdomomento")
SHOPEE_AFFILIATE_ID = os.getenv("SHOPEE_AFFILIATE_ID", "SEU_ID_SHOPEE")  # sub_id do programa
ML_AFFILIATE_ID     = os.getenv("ML_AFFILIATE_ID",   "SEU_ID_ML")        # publisher_id do ML
MIN_DISCOUNT_PCT  = int(os.getenv("MIN_DISCOUNT_PCT", "20"))  # só posta se desconto >= X%
POLL_INTERVAL_MIN = int(os.getenv("POLL_INTERVAL_MIN", "30")) # intervalo do scheduler
DB_PATH           = os.getenv("DB_PATH", "garimpeiro.db")


# ══════════════════════════════════════════════════════════════
# BANCO DE DADOS — evitar reposts
# ══════════════════════════════════════════════════════════════

def db_init():
    conn = sqlite3.connect(DB_PATH)
    # WAL: permite leituras concorrentes durante escritas.
    # Sem isso, múltiplas threads verificando duplicatas simultaneamente
    # geram "database is locked" quando o scheduler e um comando /garimpar
    # manual coincidem.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posted_deals (
            hash       TEXT PRIMARY KEY,
            title      TEXT,
            source     TEXT,
            posted_at  TEXT
        )
    """)
    conn.commit()
    conn.close()


def deal_already_posted(deal_hash: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute("SELECT 1 FROM posted_deals WHERE hash=?", (deal_hash,)).fetchone()
    conn.close()
    return row is not None


def mark_deal_posted(deal_hash: str, title: str, source: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO posted_deals VALUES (?,?,?,?)",
        (deal_hash, title, source, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════
# MODELO DE OFERTA
# ══════════════════════════════════════════════════════════════

@dataclass
class Deal:
    title      : str
    url        : str                 # URL original da oferta
    price      : Optional[float]     # Preço atual
    old_price  : Optional[float]     # Preço anterior (para calcular desconto)
    discount   : Optional[int]       # % de desconto
    source     : str                 # "shopee" | "mercadolivre" | "pelando" | "rss"
    image_url  : Optional[str] = None

    @property
    def hash(self) -> str:
        """ID único baseado no título para evitar duplicatas."""
        return hashlib.md5(self.title.lower().strip().encode()).hexdigest()

    # affiliate_url virou método async para poder chamar unshorten_url (I/O).
    # A @property não suporta await, então chamamos como: await deal.get_affiliate_url()
    async def get_affiliate_url(self) -> str:
        expanded = await unshorten_url(self.url)
        return inject_affiliate_params(expanded, self.source)


# ══════════════════════════════════════════════════════════════
# RESOLUÇÃO DE URLs ENCURTADAS
# ══════════════════════════════════════════════════════════════

# Domínios conhecidos de encurtadores de URL.
# Regex pura falha neles porque não há parâmetros visíveis na string.
SHORT_URL_DOMAINS = {
    "shope.ee", "amzn.to", "bit.ly", "t.co", "tinyurl.com",
    "ow.ly", "buff.ly", "rb.gy", "cutt.ly", "short.io",
}

async def unshorten_url(url: str) -> str:
    """
    Resolve URLs encurtadas seguindo redirects até a URL final.

    Por que HEAD e não GET?
    HEAD traz apenas os headers (sem body), então é muito mais rápido
    e consome quase zero banda — ideal para resolver centenas de links.

    Por que follow_redirects=True?
    Encurtadores podem ter múltiplos saltos (bit.ly → shope.ee → shopee.com.br).
    Seguimos todos automaticamente.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # Só resolve se for um domínio reconhecido de encurtador
    if not any(hostname.endswith(d) for d in SHORT_URL_DOMAINS):
        return url

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=8) as client:
            resp = await client.head(url)
            final_url = str(resp.url)
            log.info(f"   🔗 Unshorten: {url[:40]} → {final_url[:60]}")
            return final_url
    except Exception as e:
        log.warning(f"   ⚠️  Não foi possível resolver URL encurtada ({url}): {e}")
        return url  # Fallback: usa a URL original sem injeção


# ══════════════════════════════════════════════════════════════
# INJEÇÃO DE LINK DE AFILIADO (urllib.parse)
# ══════════════════════════════════════════════════════════════

def inject_affiliate_params(url: str, source: str) -> str:
    """
    Injeta parâmetros de afiliado de forma estruturalmente segura.

    POR QUE urllib.parse em vez de Regex pura?
    Regex em URLs tem edge cases perigosos:
      - URL já termina com '?' → adicionar '?' de novo quebra a sintaxe
      - Parâmetro já existe com valor diferente → Regex pode duplicar
      - Encoding especial (%20, +) → Regex pode corromper

    urllib.parse.parse_qs() + urlencode() garante que:
      - '?' vs '&' são escolhidos corretamente
      - Parâmetros existentes são removidos antes de adicionar os novos
      - A URL final é sempre válida

    NOTA: Esta função espera uma URL JÁ EXPANDIDA (após unshorten_url).
    """
    url = url.strip()
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    if "shopee.com.br" in hostname:
        # parse_qs preserva todos os params existentes como dict
        params = parse_qs(parsed.query, keep_blank_values=True)
        # Remove parâmetros de afiliado antigos para não duplicar
        for old_key in ("smtt", "smid", "af_id", "sub_id"):
            params.pop(old_key, None)
        # Adiciona os novos (listas, pois parse_qs retorna listas)
        params["smtt"] = ["0"]
        params["smid"] = [SHOPEE_AFFILIATE_ID]
        # urlencode com doseq=True achata as listas de volta a string
        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    if "mercadolivre.com.br" in hostname or "mercadopago.com.br" in hostname:
        params = parse_qs(parsed.query, keep_blank_values=True)
        for old_key in ("mt", "mmt", "utm_source", "utm_medium"):
            params.pop(old_key, None)
        params["mt"]  = ["CAMPANHA_01"]
        params["mmt"] = [ML_AFFILIATE_ID]
        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    return url  # Outras fontes: retorna sem alteração


# ══════════════════════════════════════════════════════════════
# FONTES DE GARIMPO
# ══════════════════════════════════════════════════════════════

# ── 1. Feed RSS do Pelando.com.br (comunidade de ofertas) ────
# TODO: não existe esses links, pensar em outra forma
PELANDO_RSS_FEEDS = [
    "https://www.pelando.com.br/api/feeds/deals?sort=hot",     # Hot do momento
    "https://www.pelando.com.br/api/feeds/deals?sort=new",     # Mais recentes
    # Você pode adicionar feeds de categorias específicas aqui
    # Ex: https://www.pelando.com.br/ofertas/celulares-e-smartphones
]

async def fetch_pelando_deals() -> list[Deal]:
    deals = []
    async with httpx.AsyncClient(timeout=15) as client:
        for feed_url in PELANDO_RSS_FEEDS:
            try:
                # feedparser funciona com URLs diretas
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:10]:  # Pega as top 10
                    title = entry.get("title", "")
                    link  = entry.get("link", "")
                    
                    if not title or not link:
                        continue

                    # Tenta extrair preço e desconto do sumário (HTML)
                    summary = entry.get("summary", "")
                    price, old_price, discount = parse_price_from_text(summary)

                    # Filtra por desconto mínimo
                    if discount and discount < MIN_DISCOUNT_PCT:
                        continue

                    # Determina fonte pelo domínio do link
                    source = "shopee" if "shopee" in link else \
                             "mercadolivre" if "mercadolivre" in link else "pelando"

                    deals.append(Deal(
                        title=title,
                        url=link,
                        price=price,
                        old_price=old_price,
                        discount=discount,
                        source=source,
                    ))

            except Exception as e:
                log.warning(f"Erro no feed {feed_url}: {e}")

    return deals


# ── 2. RSS genérico (adicione quantos quiser) ────────────────
CUSTOM_RSS_FEEDS = [
    # Adicione outros feeds de promoção aqui
    # Ex: feed de grupos de cupom, newsletters, etc.
    # "https://exemplo.com/feed/ofertas"
]

async def fetch_custom_rss() -> list[Deal]:
    deals = []
    for feed_url in CUSTOM_RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                deals.append(Deal(
                    title=entry.get("title", "Oferta do dia"),
                    url=entry.get("link", ""),
                    price=None,
                    old_price=None,
                    discount=None,
                    source="rss",
                ))
        except Exception as e:
            log.warning(f"Erro no RSS customizado {feed_url}: {e}")
    return deals


def parse_price_from_text(text: str) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """
    Tenta extrair preço, preço original e % de desconto de texto HTML.
    Padrões: R$ 49,90 | de R$ 99,90 por R$ 49,90 | -50%
    """
    # Preço com desconto: R$ XX,XX ou R$XX.XX
    prices_found = re.findall(r'R\$\s*([\d.,]+)', text)
    prices = []
    for p in prices_found:
        try:
            prices.append(float(p.replace(".", "").replace(",", ".")))
        except ValueError:
            pass

    # % desconto explícito
    disc_match = re.search(r'[-−](\d{1,3})\s*%', text)
    discount   = int(disc_match.group(1)) if disc_match else None

    if len(prices) >= 2:
        old_price = max(prices)
        price     = min(prices)
        if not discount and old_price > 0:
            discount = int(((old_price - price) / old_price) * 100)
        return price, old_price, discount

    if len(prices) == 1:
        return prices[0], None, discount

    return None, None, discount


# ══════════════════════════════════════════════════════════════
# FORMATADOR DE MENSAGEM
# ══════════════════════════════════════════════════════════════

SOURCE_LABELS = {
    "shopee"       : "🧡 Shopee",
    "mercadolivre" : "💛 Mercado Livre",
    "pelando"      : "🔥 Pelando",
    "rss"          : "🌐 Oferta",
}

def format_message(deal: Deal, aff_url: str) -> str:
    """Formata a mensagem do Telegram com Markdown. Recebe aff_url já resolvida."""
    source_label = SOURCE_LABELS.get(deal.source, "🛒 Oferta")

    lines = [f"*{deal.title}*\n"]

    # Bloco de preço
    if deal.price and deal.old_price:
        lines.append(f"~~R$ {deal.old_price:.2f}~~ → *R$ {deal.price:.2f}*")
    elif deal.price:
        lines.append(f"*R$ {deal.price:.2f}*")

    if deal.discount:
        lines.append(f"🏷️ *{deal.discount}% OFF*")

    lines.append(f"\n📦 Fonte: {source_label}")
    lines.append(f"\n🔗 [Ver oferta e comprar]({aff_url})")
    lines.append("\n_Achadinhos do Momento · @achadinhosdomomento_")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════

async def run_garimpo(bot: Bot):
    """Executa o ciclo completo de garimpo e postagem."""
    log.info("⛏️  Iniciando ciclo de garimpo...")

    # 1. Coleta de todas as fontes
    all_deals: list[Deal] = []
    all_deals += await fetch_pelando_deals()
    all_deals += await fetch_custom_rss()

    log.info(f"   Encontradas {len(all_deals)} ofertas brutas.")

    posted = 0
    for deal in all_deals:
        # 2. Verificar duplicata
        if deal_already_posted(deal.hash):
            continue

        # 3. Filtrar URL inválida
        if not deal.url or not deal.url.startswith("http"):
            continue

        # 4. Resolver URL (unshorten se necessário) e injetar afiliado
        try:
            aff_url = await deal.get_affiliate_url()
        except Exception as e:
            log.warning(f"   ⚠️  Falha ao resolver URL, usando original: {e}")
            aff_url = deal.url

        # 5. Formatar e postar
        try:
            msg = format_message(deal, aff_url)
            await bot.send_message(
                chat_id    = CHANNEL_ID,
                text       = msg,
                parse_mode = ParseMode.MARKDOWN,
                disable_web_page_preview = False,  # Exibe preview da imagem
            )
            mark_deal_posted(deal.hash, deal.title, deal.source)
            posted += 1
            log.info(f"   ✅ Postado: {deal.title[:60]}...")

            # Rate limit: espera 3 segundos entre posts
            await asyncio.sleep(3)

        except TelegramError as e:
            # [FIX-7] RESILIÊNCIA: FloodWait é subclasse de TelegramError e carrega
            # retry_after (segundos que devemos aguardar). Ignorar esse campo faz o
            # bot bater repetidamente no rate limit até o próximo ciclo, acumulando
            # erros e arriscando ban temporário. A deal NÃO é marcada como postada
            # → será retentada no próximo ciclo de forma natural.
            from telegram.error import RetryAfter
            if isinstance(e, RetryAfter):
                wait = e.retry_after + 2   # +2s de margem de segurança
                log.warning(
                    f"   ⏳ FloodWait {wait}s — '{deal.title[:40]}' retentada no próximo ciclo"
                )
                await asyncio.sleep(wait)
            else:
                log.error(f"   ❌ Erro Telegram ao postar '{deal.title[:40]}': {e}")


# ══════════════════════════════════════════════════════════════
# COMANDOS ADMIN DO BOT (use no chat privado com o bot)
# ══════════════════════════════════════════════════════════════

from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

ADMIN_USER_ID = int(os.getenv("ADMIN_TELEGRAM_USER_ID", "0"))


def is_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_USER_ID


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "🤖 *Garimpeiro Online!*\n\n"
        "Comandos disponíveis:\n"
        "/garimpar — Roda o ciclo agora\n"
        "/status   — Mostra estatísticas\n"
        "/postar <título> | <url> — Posta oferta manual\n",
        parse_mode=ParseMode.MARKDOWN
    )


async def cmd_garimpar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text("⛏️ Iniciando garimpo manual...")
    await run_garimpo(context.bot)
    await update.message.reply_text("✅ Garimpo concluído!")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    conn  = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM posted_deals").fetchone()[0]
    today = conn.execute(
        "SELECT COUNT(*) FROM posted_deals WHERE posted_at >= date('now')"
    ).fetchone()[0]
    conn.close()
    await update.message.reply_text(
        f"📊 *Status do Garimpeiro*\n\n"
        f"📦 Total postado: {total}\n"
        f"📅 Postado hoje: {today}\n"
        f"⏱️ Intervalo: {POLL_INTERVAL_MIN} min\n"
        f"💸 Desconto mínimo: {MIN_DISCOUNT_PCT}%",
        parse_mode=ParseMode.MARKDOWN
    )


async def cmd_postar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Uso: /postar Tênis Nike 🔥 | https://shopee.com.br/produto
    """
    if not is_admin(update):
        return
    try:
        text  = " ".join(context.args)
        parts = text.split("|")
        if len(parts) < 2:
            await update.message.reply_text("Uso: /postar <título> | <url>")
            return

        title, url = parts[0].strip(), parts[1].strip()
        source     = "shopee" if "shopee" in url else \
                     "mercadolivre" if "mercadolivre" in url else "rss"
        deal = Deal(title=title, url=url, price=None, old_price=None, discount=None, source=source)

        msg = format_message(deal)
        await context.bot.send_message(
            chat_id    = CHANNEL_ID,
            text       = msg,
            parse_mode = ParseMode.MARKDOWN,
        )
        mark_deal_posted(deal.hash, deal.title, deal.source)
        await update.message.reply_text("✅ Postado com sucesso!")

    except Exception as e:
        await update.message.reply_text(f"❌ Erro: {e}")


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        raise ValueError("❌ Defina a variável de ambiente TELEGRAM_BOT_TOKEN")

    db_init()
    log.info(f"🤖 Garimpeiro iniciando | Canal: {CHANNEL_ID} | Intervalo: {POLL_INTERVAL_MIN}min")

    # Cria a Application do python-telegram-bot
    app = Application.builder().token(BOT_TOKEN).build()

    # Registra comandos admin
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("garimpar", cmd_garimpar))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("postar",   cmd_postar_manual))

    # Scheduler para rodar automaticamente
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        lambda: asyncio.ensure_future(run_garimpo(app.bot)),
        trigger  = "interval",
        minutes  = POLL_INTERVAL_MIN,
        id       = "garimpo",
        max_instances = 1,          # Garante que só rode 1 instância por vez
        coalesce      = True,
    )
    scheduler.start()

    log.info("✅ Bot rodando. Pressione Ctrl+C para parar.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
