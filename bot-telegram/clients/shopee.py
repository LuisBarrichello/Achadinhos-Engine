"""
bot-telegram/clients/shopee.py — Cliente da API Shopee Affiliate.

[QF] Filtros de qualidade adicionados:
  - MIN_RATING (padrão 4.5) — filtra produtos mal avaliados
  - MIN_SOLD   (padrão 1000) — filtra produtos sem histórico de vendas
  Ambos configuráveis via env vars e aplicados ANTES de qualquer envio.
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import httpx

from models.deal import Deal

log = logging.getLogger("garimpeiro")

SHOPEE_ERR_AUTH       = "auth_error"
SHOPEE_ERR_RATE_LIMIT = "rate_limit"
SHOPEE_ERR_SERVER     = "server_error"
SHOPEE_ERR_NETWORK    = "network_error"
SHOPEE_ERR_LOGIC      = "logic_error"


class ShopeeAPI:
    BASE_URL = "https://open-api.affiliate.shopee.com.br"

    def __init__(
        self,
        app_id     : str,
        app_secret : str,
        sub_id     : str,
        timeout    : float,
        min_rating : float = 4.5,   # [QF]
        min_sold   : int   = 1000,  # [QF]
    ) -> None:
        self._app_id     = app_id
        self._app_secret = app_secret.encode("utf-8")
        self._sub_id     = sub_id
        self._timeout    = timeout
        self._min_rating = min_rating  # [QF]
        self._min_sold   = min_sold    # [QF]

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _build_signature(self, path: str, timestamp: int, body: str = "") -> str:
        base = f"{self._app_id}{timestamp}{path}{body}"
        return hmac.new(self._app_secret, base.encode("utf-8"), hashlib.sha256).hexdigest()

    def _auth_params(self, path: str, timestamp: int, body: str = "") -> dict:
        return {
            "app_id"   : self._app_id,
            "timestamp": timestamp,
            "sign"     : self._build_signature(path, timestamp, body),
        }

    # ── [QF] Helpers de qualidade ─────────────────────────────────────────────

    @staticmethod
    def _extract_rating(item: dict) -> float:
        """Extrai rating de item raw da Shopee (vários campos possíveis)."""
        raw = (
            item.get("item_rating")
            or item.get("rating_star")
            or item.get("rating")
            or item.get("score")
            or 0.0
        )
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _extract_sold(item: dict) -> int:
        """Extrai quantidade vendida de item raw da Shopee."""
        raw = (
            item.get("sold")
            or item.get("historical_sold")
            or item.get("sales")
            or item.get("item_sold")
            or 0
        )
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def _passes_quality_filter(self, item: dict) -> bool:
        """
        [QF] Retorna True apenas se o item atende rating mínimo e vendas mínimas.
        Loga o motivo de rejeição para rastreabilidade.
        """
        rating = self._extract_rating(item)
        sold   = self._extract_sold(item)

        title = (item.get("item_name") or item.get("name") or "?")[:40]

        if rating > 0 and rating < self._min_rating:
            log.debug(f"  [QF] Rejeitado rating={rating:.1f}<{self._min_rating}: {title}")
            return False
        if sold > 0 and sold < self._min_sold:
            log.debug(f"  [QF] Rejeitado sold={sold}<{self._min_sold}: {title}")
            return False
        return True

    # ── Fetch principal ───────────────────────────────────────────────────────

    async def fetch_top_products(
        self, limit: int = 20, min_discount: int = 0
    ) -> tuple[list[dict], Optional[str]]:
        path      = "/v1/shop/get_top_products"
        timestamp = int(time.time())
        params    = {
            **self._auth_params(path, timestamp),
            "page": 1, "limit": limit, "sort_type": 2,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(self.BASE_URL + path, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            return [], self._classify_http_error("fetch_top_products", e)
        except httpx.RequestError as e:
            log.error(f"Shopee rede: {e}")
            return [], SHOPEE_ERR_NETWORK

        code = data.get("code", 0) or data.get("error", 0)
        if code != 0:
            log.warning(f"Shopee erro lógico: code={code}")
            return [], SHOPEE_ERR_LOGIC

        items = (
            data.get("data", {}).get("item_list")
            or data.get("data", {}).get("items")
            or data.get("item_list")
            or []
        )

        total_before = len(items)

        # [QF] Aplica filtro de qualidade ANTES de qualquer envio
        items = [it for it in items if self._passes_quality_filter(it)]
        quality_rejected = total_before - len(items)

        if min_discount > 0:
            items = [it for it in items if self._extract_discount(it) >= min_discount]

        log.info(
            f"Shopee: {total_before} brutos | "
            f"{quality_rejected} rejeitados (QF) | "
            f"{len(items)} aprovados"
        )
        return items, None

    # ── Geração de link ───────────────────────────────────────────────────────

    async def generate_affiliate_link(self, item_url: str) -> Optional[str]:
        path      = "/v1/link/generate"
        timestamp = int(time.time())
        body_dict = {"original_url": item_url, "sub_ids": [self._sub_id]}
        body_str  = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False)
        params    = self._auth_params(path, timestamp, body_str)

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
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            log.error(f"Shopee [generate_affiliate_link]: {e}")
            return None

        code = data.get("code", 0) or data.get("error", 0)
        if code != 0:
            return None
        return (
            data.get("data", {}).get("short_link")
            or data.get("data", {}).get("affiliate_link")
            or data.get("short_link")
        )

    # ── Build deal ────────────────────────────────────────────────────────────

    async def build_deal(self, raw_item: dict) -> Optional[Deal]:
        item_id = str(raw_item.get("item_id") or raw_item.get("itemid") or "")
        shop_id = str(raw_item.get("shop_id") or raw_item.get("shopid") or "")
        title   = (raw_item.get("item_name") or raw_item.get("name") or "").strip()
        if not item_id or not title:
            return None

        slug     = title.lower().replace(" ", "-")[:60]
        item_url = f"https://shopee.com.br/{slug}-i.{shop_id}.{item_id}"
        aff_url  = await self.generate_affiliate_link(item_url)
        if not aff_url:
            return None

        def _brl(raw):
            if raw is None:
                return None
            v = float(raw)
            return round(v / 100_000, 2) if v > 100_000 else round(v, 2)

        price    = _brl(raw_item.get("price_min") or raw_item.get("price"))
        orig     = _brl(raw_item.get("price_min_before_discount") or raw_item.get("original_price"))
        discount = self._extract_discount(raw_item)
        if not discount and price and orig and orig > price:
            discount = int(((orig - price) / orig) * 100)

        img = raw_item.get("item_image") or raw_item.get("image") or raw_item.get("cover") or ""
        if img and not img.startswith("http"):
            img = "https:" + img

        # [QF] Preserva rating e sold no Deal para exibição no caption
        rating = self._extract_rating(raw_item) or None
        sold   = self._extract_sold(raw_item) or None

        return Deal(
            item_id        = item_id,
            title          = title,
            affiliate_url  = aff_url,
            price          = price,
            original_price = orig if orig != price else None,
            discount_pct   = discount or None,
            image_url      = img or None,
            shop_name      = raw_item.get("shop_name") or raw_item.get("shopName") or "",
            rating         = rating,  # [QF]
            sold           = sold,    # [QF]
        )

    # ── Helpers estáticos ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_discount(item: dict) -> int:
        raw = (
            item.get("discount")
            or item.get("discount_pct")
            or item.get("price_discount_rate")
            or 0
        )
        try:
            v = float(raw)
            return int(v * 100) if 0 < v < 1 else int(v)
        except (TypeError, ValueError):
            return 0

    def _classify_http_error(self, method: str, exc: httpx.HTTPStatusError) -> str:
        status = exc.response.status_code
        body   = exc.response.text[:200]
        if status == 429:
            log.warning(f"Shopee [{method}] rate limit (429)")
            return SHOPEE_ERR_RATE_LIMIT
        if status in (401, 403):
            log.error(f"Shopee [{method}] autenticação falhou ({status})")
            return SHOPEE_ERR_AUTH
        if status >= 500:
            log.error(f"Shopee [{method}] erro servidor ({status}): {body}")
            return SHOPEE_ERR_SERVER
        log.error(f"Shopee [{method}] HTTP {status}: {body}")
        return SHOPEE_ERR_SERVER
