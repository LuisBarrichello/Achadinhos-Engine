import logging
from typing import List
from models.deal import Deal
from models.shopee_dto import ShopeeGraphQLResponse
from clients.shopee import ShopeeGraphQLClient

log = logging.getLogger(__name__)


class ShopeeFetcherService:
    def __init__(self, client: ShopeeGraphQLClient):
        self._client = client

    def _get_top_offers_query(self) -> str:
        """
        Query GraphQL Oficial da Plataforma de Afiliados Shopee.
        Busca os produtos mais convertidos/desconto na rede.
        """
        return """
        query GetTopProducts($limit: Int!) {
            productOfferV2(listType: 0, sortType: 1, limit: $limit) {
                nodes {
                    itemId
                    shopId
                    itemName
                    price
                    originalPrice
                    discountRate
                    commissionRate
                    offerLink
                    imageUrl
                }
            }
        }
        """

    async def fetch_and_normalize_deals(self, limit: int = 15) -> List[Deal]:
        """Orquestra a busca, parse, validação e conversão."""
        try:
            # 1. Busca os dados brutos via GraphQL
            variables = {"limit": limit}
            raw_data = await self._client.execute_query(
                query=self._get_top_offers_query(),
                variables=variables
            )

            # 2. Parse e Validação Forte via Pydantic
            response_dto = ShopeeGraphQLResponse(**raw_data)

            if not response_dto.data or not response_dto.data.productOfferV2:
                log.warning("[SHOPEE_FETCHER] Retorno vazio ou estrutura inválida.")
                return []

            nodes = response_dto.data.productOfferV2.nodes
            valid_deals = []

            # 3. Conversão para o Domínio e Fingerprinting
            for node in nodes:
                try:
                    deal = Deal(
                        item_id=str(node.item_id),
                        title=node.item_name,
                        price=node.price,
                        original_price=node.original_price,
                        affiliate_url=str(node.offer_link),
                        image_url=str(node.image_url) if node.image_url else None,
                        fingerprint=node.fingerprint  # <-- Assinatura de estado!
                    )
                    valid_deals.append(deal)
                except Exception as e:
                    log.error(f"[SHOPEE_FETCHER] Erro ao normalizar produto {node.item_id}: {e}")

            log.info(f"[SHOPEE_FETCHER] {len(valid_deals)} ofertas recuperadas e normalizadas.")
            return valid_deals

        except Exception as e:
            log.error(f"[SHOPEE_FETCHER] Falha total na rotina de fetch: {e}")
            return []