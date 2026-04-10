"""
╔══════════════════════════════════════════════════════════════════╗
║         Achadinhos do Momento — Garimpeiro v3.0                  ║
║         Worker assíncrono · Shopee Affiliate Open Platform       ║
╠══════════════════════════════════════════════════════════════════╣
║  Fluxo por ciclo:                                                ║
║    1. ShopeeAPI.fetch_top_products()                             ║
║       └─ GET /v1/shop/get_top_products  (mais vendidos)          ║
║    2. ShopeeAPI.generate_affiliate_link()                        ║
║       └─ POST /v1/link/generate         (link de afiliado)       ║
║    3. DealStore.already_seen()          (antiduplicidade JSON)   ║
║    4. TelegramClient.send_deal()        (sendPhoto + caption)    ║
║    5. VitrineAPI.publish()              (POST /links na FastAPI) ║
╠══════════════════════════════════════════════════════════════════╣
║  Variáveis de ambiente (.env):                                   ║
║    SHOPEE_APP_ID        SHOPEE_APP_SECRET     SHOPEE_SUB_ID      ║
║    TELEGRAM_BOT_TOKEN   TELEGRAM_CHANNEL_ID                      ║
║    API_BASE_URL         ADMIN_SECRET                             ║
║    POLL_INTERVAL_MIN (padrão 30)                                 ║
║    MIN_DISCOUNT_PCT  (padrão 20)                                 ║
║    DEALS_PER_CYCLE   (padrão 5)                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

# ─── Bootstrap ───────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)-18s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("garimpeiro")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO CENTRAL
# ══════════════════════════════════════════════════════════════════════════════

class Config:
    """Centraliza todas as variáveis de ambiente com validação explícita.

    Chamando Config.validate() na inicialização, qualquer credencial ausente
    gera um erro claro *antes* de o loop começar — não no meio do primeiro ciclo.
    """

    # ── Shopee ────────────────────────────────────────────────────────────────
    SHOPEE_APP_ID     : str = os.getenv("SHOPEE_APP_ID",     "")
    SHOPEE_APP_SECRET : str = os.getenv("SHOPEE_APP_SECRET", "")
    # sub_id: identificador de rastreamento dentro do programa de afiliados.
    # Aparece nos relatórios do painel como origem do clique.
    SHOPEE_SUB_ID     : str = os.getenv("SHOPEE_SUB_ID", "achadinhos")

    # ── Telegram ──────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN  : str = os.getenv("TELEGRAM_BOT_TOKEN",  "")
    TELEGRAM_CHANNEL_ID : str = os.getenv("TELEGRAM_CHANNEL_ID", "")

    # ── Vitrine (FastAPI local) ───────────────────────────────────────────────
    API_BASE_URL : str = os.getenv("API_BASE_URL",  "http://localhost:8000").rstrip("/")
    ADMIN_SECRET : str = os.getenv("ADMIN_SECRET",  "")

    # ── Comportamento do loop ─────────────────────────────────────────────────
    POLL_INTERVAL_MIN : int   = int(os.getenv("POLL_INTERVAL_MIN", "30"))
    MIN_DISCOUNT_PCT  : int   = int(os.getenv("MIN_DISCOUNT_PCT",  "20"))
    DEALS_PER_CYCLE   : int   = int(os.getenv("DEALS_PER_CYCLE",   "5"))
    HTTP_TIMEOUT      : float = float(os.getenv("HTTP_TIMEOUT",    "20.0"))

    PROCESSED_DEALS_PATH : Path = Path(
        os.getenv("PROCESSED_DEALS_PATH", "processed_deals.json")
    )

    @classmethod
    def validate(cls) -> None:
        """Verifica credenciais obrigatórias e avisa sobre as opcionais ausentes."""
        must_have = {
            "SHOPEE_APP_ID"      : cls.SHOPEE_APP_ID,
            "SHOPEE_APP_SECRET"  : cls.SHOPEE_APP_SECRET,
            "TELEGRAM_BOT_TOKEN" : cls.TELEGRAM_BOT_TOKEN,
            "TELEGRAM_CHANNEL_ID": cls.TELEGRAM_CHANNEL_ID,
            "ADMIN_SECRET"       : cls.ADMIN_SECRET,
        }
        missing = [k for k, v in must_have.items() if not v]
        if missing:
            raise EnvironmentError(
                "Variáveis de ambiente obrigatórias não definidas:\n"
                + "\n".join(f"  · {k}" for k in missing)
                + "\n\nConsulte o .env.example e preencha o .env antes de rodar."
            )
        log.info("✅ Configuração validada com sucesso.")


# ══════════════════════════════════════════════════════════════════════════════
# MODELO DE DADOS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Deal:
    """Oferta normalizada, independente do formato bruto da API da Shopee.

    Centralizar a representação aqui significa que TelegramClient e
    VitrineAPI nunca precisam conhecer o formato cru da Shopee.
    """
    item_id        : str
    title          : str
    affiliate_url  : str            # link gerado pelo endpoint /v1/link/generate
    price          : Optional[float]
    original_price : Optional[float]
    discount_pct   : Optional[int]  # ex: 35  (= 35% de desconto)
    image_url      : Optional[str]
    shop_name      : str = ""

    @property
    def unique_key(self) -> str:
        """Chave de deduplicação usada pelo DealStore."""
        return f"shopee:{self.item_id}"

    def to_vitrine_payload(self) -> dict:
        """Serializa para o payload esperado pelo endpoint POST /links da FastAPI."""
        title_with_discount = (
            f"{self.title} — {self.discount_pct}% OFF"
            if self.discount_pct
            else self.title
        )
        return {
            "title"      : title_with_discount[:120],
            "url"        : self.affiliate_url,
            "emoji"      : "🛍️",
            "badge"      : "SHOPEE",
            "badge_color": "#ee4d2d",
            "order"      : 10,
            "image_url"  : self.image_url,
        }

    def to_telegram_caption(self) -> str:
        """Formata a legenda da mensagem do Telegram (Markdown simples).

        Usamos parse_mode=Markdown (v1) em vez de MarkdownV2 para evitar
        problemas de escape com pontuação vinda de títulos de produtos externos.
        """
        lines: list[str] = []

        lines.append(f"*{self.title}*")
        lines.append("")

        if self.price is not None and self.original_price is not None:
            lines.append(f"~~R$ {self.original_price:,.2f}~~  ->  *R$ {self.price:,.2f}*")
        elif self.price is not None:
            lines.append(f"*R$ {self.price:,.2f}*")

        if self.discount_pct:
            lines.append(f"*{self.discount_pct}% OFF*")

        if self.shop_name:
            lines.append(f"Loja: {self.shop_name}")

        lines.append("")
        lines.append(f"[Ver oferta na Shopee]({self.affiliate_url})")
        lines.append("")
        lines.append("_Achadinhos do Momento_")

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# DEAL STORE — ANTIDUPLICIDADE
# ══════════════════════════════════════════════════════════════════════════════

class DealStore:
    """Persiste IDs de deals já publicadas em um arquivo JSON local.

    Estratégia de escrita lazy:
        Acumulamos novas chaves em memória durante o ciclo e persistimos
        no disco somente ao final via flush(). Isso evita múltiplas escritas
        por ciclo. Se o processo crashar, reprocessamos no máximo um ciclo
        inteiro — aceitável para um bot de afiliados.

    Formato do arquivo:
        { "shopee:123456": 1718000000, "shopee:789012": 1718003600 }
        Valor = unix timestamp de publicação (útil para auditoria futura).
    """

    def __init__(self, path: Path) -> None:
        self._path  = path
        self._store : dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        if not self._path.exists():
            log.info(f"DealStore: {self._path} não encontrado, iniciando vazio.")
            return {}
        try:
            with self._path.open(encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                return {k: 0 for k in raw}  # compatibilidade com formato antigo
            if isinstance(raw, dict):
                return raw
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"DealStore: erro ao ler {self._path}: {e} — iniciando vazio.")
        return {}

    def already_seen(self, key: str) -> bool:
        return key in self._store

    def mark(self, key: str) -> None:
        """Registra em memória. Chame flush() ao fim do ciclo para persistir."""
        self._store[key] = int(time.time())

    def flush(self) -> None:
        """Salva o estado em disco usando write-then-rename atômico."""
        tmp = self._path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._store, f, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
            log.debug(f"DealStore: {len(self._store)} entradas salvas em {self._path}")
        except IOError as e:
            log.error(f"DealStore: falha ao persistir: {e}")
            if tmp.exists():
                tmp.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# SHOPEE API CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class ShopeeAPI:
    """Cliente para a Shopee Affiliate Open Platform.

    ── Endpoints utilizados ──────────────────────────────────────────────────
    1. GET  /v1/shop/get_top_products
       Retorna os N produtos mais vendidos da plataforma inteira.
       Parâmetros: page, limit, sort_type (2 = mais vendidos).
       Docs: https://open-api.affiliate.shopee.com.br/docs#/product

    2. POST /v1/link/generate
       Dado um item_id + shop_id (ou URL canônica do produto), gera o link
       curto de afiliado com seu sub_id embutido para rastreamento de comissão.
       Docs: https://open-api.affiliate.shopee.com.br/docs#/link

    ── Autenticação HMAC-SHA256 ──────────────────────────────────────────────
    Cada requisição precisa dos seguintes parâmetros (query string ou body):
        app_id    → seu App ID do painel Open Platform
        timestamp → unix timestamp em segundos (válido ±300s da hora atual)
        sign      → HMAC-SHA256(app_secret, f"{app_id}{timestamp}{path}{body}")

    Para requisições GET: body = "" (string vazia).
    Para requisições POST: body = string JSON exata enviada no body.

    Referência do algoritmo:
        https://open-api.affiliate.shopee.com.br/docs#/auth
    """

    BASE_URL = "https://open-api.affiliate.shopee.com.br"

    def __init__(
        self,
        app_id    : str,
        app_secret: str,
        sub_id    : str,
        timeout   : float,
    ) -> None:
        self._app_id     = app_id
        self._app_secret = app_secret.encode("utf-8")  # pré-encodado para reuso
        self._sub_id     = sub_id
        self._timeout    = timeout

    # ── Geração de assinatura ─────────────────────────────────────────────────

    def _build_signature(self, path: str, timestamp: int, body: str = "") -> str:
        """Gera o HMAC-SHA256 exigido pela Shopee para autenticar cada requisição.

        O algoritmo oficial concatena quatro elementos em ordem estrita:
            base_string = app_id + timestamp + path + body

        Args:
            path:      Caminho do endpoint sem domínio e sem query string.
                       Ex: "/v1/shop/get_top_products"
            timestamp: Inteiro Unix em segundos — deve ser idêntico ao
                       parâmetro `timestamp` enviado na requisição.
            body:      String JSON do body exatamente como será enviada.
                       Para GET, passe "" (string vazia — padrão).

        Returns:
            Hex digest lowercase da assinatura.
        """
        base_string = f"{self._app_id}{timestamp}{path}{body}"
        return hmac.new(
            self._app_secret,
            base_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _auth_params(self, path: str, timestamp: int, body: str = "") -> dict:
        """Monta o dict de autenticação pronto para injetar na query string."""
        return {
            "app_id"   : self._app_id,
            "timestamp": timestamp,
            "sign"     : self._build_signature(path, timestamp, body),
        }

    # ── Endpoint 1: produtos mais vendidos ───────────────────────────────────

    async def fetch_top_products(
        self,
        limit       : int = 20,
        min_discount: int = 0,
    ) -> list[dict]:
        """Busca os produtos mais vendidos via GET /v1/shop/get_top_products.

        Args:
            limit:        Quantidade de produtos a solicitar (máx: 50).
            min_discount: Desconto mínimo em % — filtrado localmente
                          pois este endpoint não tem parâmetro nativo.

        Returns:
            Lista de dicts brutos da API. Lista vazia em caso de qualquer erro.
        """
        path      = "/v1/shop/get_top_products"
        timestamp = int(time.time())

        params = {
            **self._auth_params(path, timestamp),
            "page"     : 1,
            "limit"    : limit,
            "sort_type": 2,     # 2 = mais vendidos; 1 = mais recentes
        }

        log.info(f"Shopee: buscando top {limit} produtos...")

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(self.BASE_URL + path, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            self._handle_http_error("fetch_top_products", e)
            return []
        except httpx.RequestError as e:
            log.error(f"Shopee [fetch_top_products] erro de rede: {e}")
            return []

        # A Shopee pode retornar HTTP 200 com um erro lógico no body
        code = data.get("code", 0) or data.get("error", 0)
        if code != 0:
            log.warning(
                f"Shopee erro lógico: code={code} "
                f"msg={data.get('message') or data.get('msg', '')}"
            )
            return []

        # A estrutura aninhada varia entre versões — verificamos os caminhos comuns
        items: list[dict] = (
            data.get("data", {}).get("item_list")
            or data.get("data", {}).get("items")
            or data.get("item_list")
            or []
        )

        if min_discount > 0:
            items = [it for it in items if self._extract_discount(it) >= min_discount]

        log.info(f"Shopee: {len(items)} produtos após filtro de >= {min_discount}% desconto")
        return items

    # ── Endpoint 2: gerar link de afiliado ───────────────────────────────────

    async def generate_affiliate_link(self, item_url: str) -> Optional[str]:
        """Gera o link curto de afiliado via POST /v1/link/generate.

        A Shopee exige a URL canônica do produto no body. O link retornado
        já contém o sub_id embutido para rastreamento de comissão.

        Args:
            item_url: URL canônica do produto na Shopee
                      (ex: https://shopee.com.br/produto-i.shop_id.item_id)

        Returns:
            String com o link de afiliado, ou None em caso de erro.
        """
        path      = "/v1/link/generate"
        timestamp = int(time.time())

        body_dict = {
            "original_url": item_url,
            "sub_ids"     : [self._sub_id],
        }
        # Serialização compacta (sem espaços) — deve ser idêntica ao que vai no body
        body_str = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False)

        params = self._auth_params(path, timestamp, body_str)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self.BASE_URL + path,
                    content=body_str,
                    headers={"Content-Type": "application/json"},
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            self._handle_http_error("generate_affiliate_link", e)
            return None
        except httpx.RequestError as e:
            log.error(f"Shopee [generate_affiliate_link] erro de rede: {e}")
            return None

        code = data.get("code", 0) or data.get("error", 0)
        if code != 0:
            log.warning(
                f"Shopee [generate_affiliate_link] erro lógico: code={code} "
                f"msg={data.get('message') or data.get('msg', '')}"
            )
            return None

        # O campo do link varia entre versões da API
        link = (
            data.get("data", {}).get("short_link")
            or data.get("data", {}).get("affiliate_link")
            or data.get("short_link")
        )
        return link or None

    # ── Normalização: item bruto da API → Deal ────────────────────────────────

    async def build_deal(self, raw_item: dict) -> Optional[Deal]:
        """Converte um item bruto da API Shopee para o modelo Deal unificado.

        Gera o link de afiliado durante a construção (1 chamada à API por item).
        Se a geração falhar, o item é descartado — nunca postamos link inválido.

        Sobre a unidade de preço da Shopee:
            Preços são retornados em "centésimos de centavo":
            R$ 49,90 → 4.990.000 (divides por 100.000 para obter BRL)
            Porém algumas versões da API já retornam em BRL diretamente.
            A lógica abaixo detecta e trata ambos os casos.
        """
        item_id  = str(raw_item.get("item_id") or raw_item.get("itemid") or "")
        shop_id  = str(raw_item.get("shop_id") or raw_item.get("shopid") or "")
        title    = (raw_item.get("item_name") or raw_item.get("name") or "").strip()

        if not item_id or not title:
            log.debug("build_deal: item_id ou title ausente, descartando")
            return None

        # Constrói URL canônica a partir dos IDs (a API não a retorna diretamente)
        slug = title.lower().replace(" ", "-")[:60]
        item_url = f"https://shopee.com.br/{slug}-i.{shop_id}.{item_id}"

        # Gera link de afiliado — se falhar, descarta o item
        affiliate_url = await self.generate_affiliate_link(item_url)
        if not affiliate_url:
            log.warning(f"build_deal: não foi possível gerar link para item {item_id}")
            return None

        # ── Preços ────────────────────────────────────────────────────────────
        def _to_brl(raw: Optional[int | float]) -> Optional[float]:
            if raw is None:
                return None
            v = float(raw)
            # Valores > 1.000 em BRL seriam absurdos para maioria dos produtos,
            # mas > 1.000 em centésimos de centavo é normal (R$ 10,00 = 1.000.000).
            # Heurística: se o valor > 100.000, assume que está na unidade da Shopee.
            return round(v / 100_000, 2) if v > 100_000 else round(v, 2)

        price = _to_brl(
            raw_item.get("price_min")
            or raw_item.get("price")
            or raw_item.get("min_price")
        )
        original_price = _to_brl(
            raw_item.get("price_min_before_discount")
            or raw_item.get("original_price")
            or raw_item.get("market_price")
        )

        discount = self._extract_discount(raw_item)
        if not discount and price and original_price and original_price > price:
            discount = int(((original_price - price) / original_price) * 100)

        # ── Imagem ────────────────────────────────────────────────────────────
        image_raw = (
            raw_item.get("item_image")
            or raw_item.get("image")
            or raw_item.get("cover")
            or ""
        )
        if image_raw and not image_raw.startswith("http"):
            image_raw = "https:" + image_raw  # corrige URLs sem protocolo

        return Deal(
            item_id        = item_id,
            title          = title,
            affiliate_url  = affiliate_url,
            price          = price,
            original_price = original_price if original_price != price else None,
            discount_pct   = discount or None,
            image_url      = image_raw or None,
            shop_name      = raw_item.get("shop_name") or raw_item.get("shopName") or "",
        )

    # ── Helpers privados ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_discount(item: dict) -> int:
        """Extrai o percentual de desconto de um item com múltiplos fallbacks."""
        raw = (
            item.get("discount")
            or item.get("discount_pct")
            or item.get("price_discount_rate")
            or 0
        )
        try:
            v = float(raw)
            return int(v * 100) if 0 < v < 1 else int(v)  # suporta fração (0.35) ou inteiro (35)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _handle_http_error(method: str, exc: httpx.HTTPStatusError) -> None:
        """Loga erros HTTP com mensagens de diagnóstico específicas por código."""
        status = exc.response.status_code
        body   = exc.response.text[:200]
        if status == 429:
            log.warning(f"Shopee [{method}] rate limit (429) — aguardará próximo ciclo")
        elif status in (401, 403):
            log.error(f"Shopee [{method}] autenticação falhou ({status}) — verifique App ID/Secret")
        elif status >= 500:
            log.error(f"Shopee [{method}] erro no servidor ({status}): {body}")
        else:
            log.error(f"Shopee [{method}] HTTP {status}: {body}")


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class TelegramClient:
    """Envia fotos e mensagens para um canal do Telegram via Bot API.

    Usa httpx diretamente, sem bibliotecas de alto nível, para manter
    consistência com o restante do projeto.

    Rate limits do Telegram:
        · 1 mensagem/segundo por chat
        · 20 mensagens/minuto para o mesmo grupo/canal
        O _SEND_DELAY entre envios respeita esses limites com margem.
    """

    TELEGRAM_API = "https://api.telegram.org/bot"
    _SEND_DELAY  = 2.5  # segundos entre mensagens consecutivas

    def __init__(self, token: str, channel_id: str, timeout: float) -> None:
        self._base       = f"{self.TELEGRAM_API}{token}"
        self._channel_id = channel_id
        self._timeout    = timeout

    async def send_deal(self, deal: Deal) -> bool:
        """Envia a deal como foto com legenda para o canal.

        Fallback em dois níveis:
            1. sendPhoto  (imagem + legenda) — ideal para canal de ofertas
            2. sendMessage (só texto)        — se não houver imagem ou sendPhoto falhar

        Returns:
            True se a mensagem chegou ao canal.
        """
        caption = deal.to_telegram_caption()

        if deal.image_url:
            ok = await self._send_photo(deal.image_url, caption)
            if ok:
                await asyncio.sleep(self._SEND_DELAY)
                return True
            log.warning(
                f"Telegram: sendPhoto falhou para '{deal.title[:40]}' "
                "— tentando sendMessage como fallback"
            )

        ok = await self._send_message(caption)
        await asyncio.sleep(self._SEND_DELAY)
        return ok

    async def _send_photo(self, photo_url: str, caption: str) -> bool:
        return await self._post("sendPhoto", {
            "chat_id"   : self._channel_id,
            "photo"     : photo_url,
            "caption"   : caption,
            "parse_mode": "Markdown",
        })

    async def _send_message(self, text: str) -> bool:
        return await self._post("sendMessage", {
            "chat_id"                  : self._channel_id,
            "text"                     : text,
            "parse_mode"               : "Markdown",
            "disable_web_page_preview" : False,
        })

    async def _post(self, method: str, payload: dict) -> bool:
        """Executor genérico de POST para qualquer método da Bot API.

        Trata:
            429 → lê retry_after e aguarda
            400 → Bad Request (ex: URL de foto inválida) — não retenta
            5xx → erro temporário do Telegram — loga sem crash
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}/{method}", json=payload)

            if resp.status_code == 429:
                retry = resp.json().get("parameters", {}).get("retry_after", 60)
                log.warning(f"Telegram rate limit em {method} — aguardando {retry}s")
                await asyncio.sleep(retry + 2)
                return False

            if resp.status_code == 400:
                log.warning(f"Telegram 400 em {method}: {resp.text[:150]}")
                return False

            resp.raise_for_status()
            return True

        except httpx.TimeoutException:
            log.error(f"Telegram: timeout em {method}")
            return False
        except httpx.RequestError as e:
            log.error(f"Telegram: erro de rede em {method}: {e}")
            return False
        except Exception as e:
            log.error(f"Telegram: erro inesperado em {method}: {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════════
# VITRINE API CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class VitrineAPI:
    """Publica deals na vitrine web via POST /links da FastAPI local.

    Tratada como "melhor esforço": se a API estiver offline, o bot
    continua postando no Telegram normalmente, apenas logando o erro.
    """

    def __init__(self, base_url: str, admin_secret: str, timeout: float) -> None:
        self._base    = base_url
        self._timeout = timeout
        self._headers = {
            "Content-Type"  : "application/json",
            "x-admin-secret": admin_secret,
        }

    async def publish(self, deal: Deal) -> bool:
        """Cria um novo link na vitrine via POST /links.

        Returns:
            True se o link foi criado com sucesso (HTTP 200/201).
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/links",
                    json=deal.to_vitrine_payload(),
                    headers=self._headers,
                )

            if resp.status_code == 403:
                log.error("Vitrine: ADMIN_SECRET inválido (403) — verifique a variável de ambiente")
                return False

            resp.raise_for_status()
            link_id = resp.json().get("id", "?")
            log.info(f"Vitrine: link #{link_id} criado para '{deal.title[:50]}'")
            return True

        except httpx.HTTPStatusError as e:
            log.error(f"Vitrine HTTP {e.response.status_code}: {e.response.text[:150]}")
            return False
        except httpx.RequestError as e:
            log.warning(f"Vitrine inacessível (API offline?): {e}")
            return False
        except Exception as e:
            log.error(f"Vitrine erro inesperado: {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════════
# GARIMPEIRO — ORQUESTRADOR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class Garimpeiro:
    """Orquestra o ciclo completo de garimpo e mantém o loop 24/7.

    Responsabilidades:
        · Disparar as buscas na Shopee
        · Filtrar duplicatas via DealStore
        · Publicar no Telegram e na Vitrine em sequência
        · Garantir que erros pontuais nunca derrubem o processo
    """

    def __init__(self, cfg: type[Config]) -> None:
        self._cfg   = cfg
        self._store = DealStore(cfg.PROCESSED_DEALS_PATH)

        self._shopee = ShopeeAPI(
            app_id     = cfg.SHOPEE_APP_ID,
            app_secret = cfg.SHOPEE_APP_SECRET,
            sub_id     = cfg.SHOPEE_SUB_ID,
            timeout    = cfg.HTTP_TIMEOUT,
        )
        self._telegram = TelegramClient(
            token      = cfg.TELEGRAM_BOT_TOKEN,
            channel_id = cfg.TELEGRAM_CHANNEL_ID,
            timeout    = cfg.HTTP_TIMEOUT,
        )
        self._vitrine = VitrineAPI(
            base_url     = cfg.API_BASE_URL,
            admin_secret = cfg.ADMIN_SECRET,
            timeout      = cfg.HTTP_TIMEOUT,
        )

    async def run_cycle(self) -> None:
        """Executa um ciclo completo: busca → filtra → normaliza → publica."""
        log.info("─" * 60)
        log.info("⛏️  Iniciando ciclo de garimpo")

        # ── 1. Busca produtos mais vendidos ───────────────────────────────────
        # Pedimos 4× DEALS_PER_CYCLE para ter margem após filtrar duplicatas
        raw_items = await self._shopee.fetch_top_products(
            limit        = self._cfg.DEALS_PER_CYCLE * 4,
            min_discount = self._cfg.MIN_DISCOUNT_PCT,
        )

        if not raw_items:
            log.info("Shopee: nenhum produto retornado neste ciclo.")
            return

        # ── 2. Filtra duplicatas ANTES de gerar links de afiliado ─────────────
        # Cada geração de link consome uma chamada de API.
        # Filtrar antes evita chamadas desnecessárias para itens já postados.
        new_items = [
            it for it in raw_items
            if not self._store.already_seen(
                f"shopee:{it.get('item_id') or it.get('itemid', '')}"
            )
        ]

        to_process = new_items[: self._cfg.DEALS_PER_CYCLE]
        log.info(
            f"📦 {len(raw_items)} brutos | "
            f"{len(new_items)} novos | "
            f"processando {len(to_process)}"
        )

        if not to_process:
            log.info("Nenhum item novo para processar neste ciclo.")
            return

        # ── 3. Normaliza items → Deals (gera links de afiliado) ───────────────
        # Sequencial (não paralelo) para respeitar o rate limit da API de links
        deals: list[Deal] = []
        for raw in to_process:
            deal = await self._shopee.build_deal(raw)
            if deal:
                deals.append(deal)
            await asyncio.sleep(0.5)  # pausa entre chamadas ao endpoint de link

        if not deals:
            log.info("Nenhuma deal válida após normalização.")
            return

        # ── 4. Publica ────────────────────────────────────────────────────────
        telegram_ok = 0
        vitrine_ok  = 0

        for deal in deals:
            log.info(f"  Postando [{deal.item_id}]: {deal.title[:55]}")

            # Telegram é obrigatório — só segue para vitrine se conseguir postar
            tg_ok = await self._telegram.send_deal(deal)
            if not tg_ok:
                log.warning(
                    f"  Telegram falhou para '{deal.title[:40]}' "
                    "— não marcado como processado, tentará no próximo ciclo"
                )
                continue

            self._store.mark(deal.unique_key)
            telegram_ok += 1

            vt_ok = await self._vitrine.publish(deal)
            if vt_ok:
                vitrine_ok += 1

        # ── 5. Persiste estado em disco ───────────────────────────────────────
        self._store.flush()

        log.info(
            f"Ciclo concluído — "
            f"Telegram: {telegram_ok}/{len(deals)} | "
            f"Vitrine: {vitrine_ok}/{len(deals)}"
        )

    async def run_forever(self) -> None:
        """Loop 24/7 com intervalo configurável via POLL_INTERVAL_MIN.

        Qualquer exceção não prevista em run_cycle é capturada, logada e
        o loop continua — o bot nunca cai por um bug pontual.
        """
        interval = self._cfg.POLL_INTERVAL_MIN * 60

        log.info("=" * 60)
        log.info("Garimpeiro iniciado")
        log.info(f"  Intervalo     : {self._cfg.POLL_INTERVAL_MIN} min")
        log.info(f"  Desconto min. : {self._cfg.MIN_DISCOUNT_PCT}%")
        log.info(f"  Deals/ciclo   : {self._cfg.DEALS_PER_CYCLE}")
        log.info(f"  Store         : {self._cfg.PROCESSED_DEALS_PATH}")
        log.info("=" * 60)

        while True:
            try:
                await self.run_cycle()
            except Exception as exc:
                log.exception(f"Exceção não tratada em run_cycle: {exc}")

            next_time = time.strftime("%H:%M:%S", time.localtime(time.time() + interval))
            log.info(f"Dormindo {self._cfg.POLL_INTERVAL_MIN}min — próximo ciclo às {next_time}")
            await asyncio.sleep(interval)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    Config.validate()
    await Garimpeiro(Config).run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Garimpeiro encerrado pelo usuário (Ctrl+C)")
    except EnvironmentError as exc:
        log.critical(str(exc))
        raise SystemExit(1)
