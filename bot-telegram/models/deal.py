from dataclasses import dataclass
from typing import Optional


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

    @property
    def unique_key(self) -> str:
        return f"shopee:{self.item_id}"

    def to_vitrine_payload(self) -> dict:
        title_with_discount = (
            f"{self.title} — {self.discount_pct}% OFF"
            if self.discount_pct else self.title
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
        lines: list[str] = [f"*{self.title}*", ""]
        if self.price is not None and self.original_price is not None:
            lines.append(f"~~R$ {self.original_price:,.2f}~~  ->  *R$ {self.price:,.2f}*")
        elif self.price is not None:
            lines.append(f"*R$ {self.price:,.2f}*")
        if self.discount_pct:
            lines.append(f"*{self.discount_pct}% OFF*")
        if self.shop_name:
            lines.append(f"Loja: {self.shop_name}")
        lines += [
            "",
            f"[Ver oferta na Shopee]({self.affiliate_url})",
            "",
            "_Achadinhos do Momento_",
        ]
        return "\n".join(lines)
