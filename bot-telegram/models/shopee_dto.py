import hashlib
from typing import List, Optional
from pydantic import BaseModel, Field, HttpUrl

class ShopeeOfferNode(BaseModel):
    """Representa um nó de produto retornado pelo GraphQL da Shopee"""
    item_id: str = Field(alias="itemId")
    shop_id: str = Field(alias="shopId")
    item_name: str = Field(alias="itemName")
    price: float = Field(alias="price")
    original_price: Optional[float] = Field(None, alias="originalPrice")
    discount_rate: Optional[float] = Field(0.0, alias="discountRate")
    commission_rate: Optional[float] = Field(0.0, alias="commissionRate")
    offer_link: HttpUrl = Field(alias="offerLink")
    image_url: Optional[HttpUrl] = Field(None, alias="imageUrl")

    @property
    def fingerprint(self) -> str:
        """
        DEDUPLICAÇÃO DE PRODUTO: Cria um Hash único para o estado atual da oferta.
        Se a Shopee mudar o preço, o hash muda. Se o preço for o mesmo,
        sabemos que é a mesma oferta e evitamos floodar o Telegram com falsas promoções.
        """
        raw_string = f"{self.item_id}_{self.shop_id}_{self.price:.2f}_{self.discount_rate}"
        return hashlib.sha256(raw_string.encode('utf-8')).hexdigest()

class ShopeeProductOfferV2(BaseModel):
    nodes: List[ShopeeOfferNode]

class ShopeeGraphQLData(BaseModel):
    productOfferV2: Optional[ShopeeProductOfferV2] = None

class ShopeeGraphQLResponse(BaseModel):
    data: Optional[ShopeeGraphQLData] = None
    errors: Optional[list] = None