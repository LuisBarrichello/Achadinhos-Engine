"""
bot-telegram/models/deal.py — Modelo de deal com melhorias de caption.

[CAPTION] Emojis dinâmicos por categoria do produto
[BP]      Flag is_price_bug para formatação diferenciada
[QF]      Campos rating e sold para filtros de qualidade
"""

from dataclasses import dataclass, field
from typing import Optional
import re


# ── Mapeamento de categoria → emojis ─────────────────────────────────────────
# Chave: substring lowercase que aparece no título
_CATEGORY_EMOJIS: list[tuple[list[str], str]] = [
    # Eletrônicos e tecnologia
    (["celular", "smartphone", "iphone", "samsung", "xiaomi", "redmi", "poco"], "📱⚡"),
    (["fone", "headphone", "earphone", "airpod", "bluetooth", "tws", "speaker"], "🎧🔊"),
    (["notebook", "laptop", "macbook", "computador"], "💻⚡"),
    (["tv", "televisão", "smart tv", "monitor", "tela"], "📺✨"),
    (["carregador", "cabo", "usb", "power bank", "bateria externa"], "🔌⚡"),
    (["câmera", "camera", "gopro", "drone", "tripé"], "📷🎬"),
    (["tablet", "ipad", "kindle"], "📟✨"),
    (["game", "joystick", "controle", "playstation", "xbox", "nintendo"], "🎮🕹️"),
    (["relógio", "relogio", "smartwatch", "watch"], "⌚✨"),
    # Moda e vestuário
    (["tênis", "tenis", "nike", "adidas", "puma", "vans", "sapatênis"], "👟🔥"),
    (["sapato", "sandália", "sandalia", "chinelo", "tamanco"], "👠🛍️"),
    (["camiseta", "camisa", "blusa", "moletom", "jaqueta", "casaco"], "👕🔥"),
    (["calça", "calca", "bermuda", "short", "saia", "vestido"], "👖✨"),
    (["mochila", "bolsa", "carteira", "necessaire"], "👜🛍️"),
    (["perfume", "colônia", "colonia", "desodorante"], "🌸✨"),
    (["maquiagem", "batom", "base", "rímel", "rimel", "skincare"], "💄✨"),
    # Casa e decoração
    (["sofá", "sofa", "cadeira", "mesa", "armário", "armario", "cama"], "🏠✨"),
    (["panela", "frigideira", "airfryer", "air fryer", "liquidificador"], "🍳✨"),
    (["aspirador", "robô", "robo", "vassoura", "mop"], "🧹✨"),
    (["luminária", "luminaria", "lâmpada", "lampada", "iluminação"], "💡✨"),
    (["tapete", "cortina", "almofada", "travesseiro", "colchão"], "🏠🛏️"),
    # Brinquedos e bebês
    (["brinquedo", "boneca", "lego", "quebra-cabeça", "jogo"], "🧸🎉"),
    (["bebê", "bebe", "infantil", "criança", "fraldas", "carrinho"], "👶🎀"),
    # Esporte e fitness
    (["academia", "musculação", "haltere", "anilha", "proteína"], "💪🏋️"),
    (["bicicleta", "patins", "skate", "scooter"], "🚴⚡"),
    (["yoga", "pilates", "treino", "esporte", "futebol", "chuteira"], "⚽🏃"),
    # Alimentação e saúde
    (["suplemento", "whey", "vitamina", "remédio"], "💊💪"),
    (["café", "cafe", "nespresso", "capuccino"], "☕✨"),
    # Pet
    (["pet", "cachorro", "gato", "ração", "coleira", "aquário"], "🐾❤️"),
    # Automotivo
    (["carro", "moto", "pneu", "volante", "automotivo", "veicular"], "🚗⚡"),
    # Livros e educação
    (["livro", "curso", "ebook"], "📚✨"),
]

_DEFAULT_EMOJIS = "🛍️🔥"


def _detect_emojis(title: str) -> str:
    """Retorna par de emojis baseado em keywords do título."""
    t = title.lower()
    for keywords, emojis in _CATEGORY_EMOJIS:
        if any(kw in t for kw in keywords):
            return emojis
    return _DEFAULT_EMOJIS


@dataclass
class Deal:
    item_id        : str
    title          : str
    affiliate_url  : str
    price          : Optional[float]
    original_price : Optional[float]
    discount_pct   : Optional[int]
    image_url      : Optional[str]
    shop_name      : str = ""

    # [QF] Campos de qualidade — podem ser None se a API não retornar
    rating         : Optional[float] = None
    sold           : Optional[int]   = None

    # [BP] Flag de bug de preço — setada externamente pelo garimpeiro
    is_price_bug   : bool = False

    @property
    def unique_key(self) -> str:
        return f"shopee:{self.item_id}"

    @property
    def category_emojis(self) -> str:
        """Par de emojis dinâmico baseado na categoria detectada no título."""
        return _detect_emojis(self.title)

    def to_vitrine_payload(self) -> dict:
        title_with_discount = (
            f"{self.title} — {self.discount_pct}% OFF"
            if self.discount_pct else self.title
        )
        badge = "🐛 BUG" if self.is_price_bug else "SHOPEE"
        badge_color = "#7c3aed" if self.is_price_bug else "#ee4d2d"
        return {
            "title"      : title_with_discount[:120],
            "url"        : self.affiliate_url,
            "emoji"      : self.category_emojis.split()[0],
            "badge"      : badge,
            "badge_color": badge_color,
            "order"      : 0 if self.is_price_bug else 10,
            "image_url"  : self.image_url,
        }

    def to_telegram_caption(self, is_repost: bool = False) -> str:
        """
        [CAPTION] Gera caption com emojis dinâmicos por categoria.
        [BP]      Bug de preço recebe formatação diferenciada e urgente.
        """
        emojis = self.category_emojis

        # ── Cabeçalho ─────────────────────────────────────────────────────────
        if self.is_price_bug:
            header = (
                "🚨🐛 *BUG DE PREÇO DETECTADO!* 🐛🚨\n"
                "⚠️ _Pode expirar a qualquer momento — corra!_\n\n"
            )
        elif is_repost:
            header = f"🔁 *REPOST* {emojis}\n\n"
        else:
            header = f"{emojis}\n\n"

        lines: list[str] = [header + f"*{self.title}*", ""]

        # ── Preços ────────────────────────────────────────────────────────────
        if self.price is not None and self.original_price is not None:
            lines.append(f"~~R$ {self.original_price:,.2f}~~  →  *R$ {self.price:,.2f}*")
        elif self.price is not None:
            lines.append(f"*R$ {self.price:,.2f}*")

        if self.discount_pct:
            disc_str = f"*{self.discount_pct}% OFF*"
            if self.is_price_bug:
                disc_str = f"🔥🔥🔥 *{self.discount_pct}% OFF — PREÇO IRREAL!* 🔥🔥🔥"
            lines.append(disc_str)

        # ── Rating/vendas ─────────────────────────────────────────────────────
        meta_parts: list[str] = []
        if self.rating is not None:
            meta_parts.append(f"⭐ {self.rating:.1f}")
        if self.sold is not None:
            sold_fmt = f"{self.sold:,}".replace(",", ".")
            meta_parts.append(f"📦 {sold_fmt} vendidos")
        if meta_parts:
            lines.append(" · ".join(meta_parts))

        if self.shop_name:
            lines.append(f"🏪 {self.shop_name}")

        lines += [
            "",
            f"[🛒 Ver oferta na Shopee]({self.affiliate_url})",
            "",
            "_Achadinhos do Momento_",
        ]
        return "\n".join(lines)
