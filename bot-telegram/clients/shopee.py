import httpx
import asyncio
import logging
from typing import Optional

from core.exceptions import ShopeeAPIError, ShopeeAuthError, ShopeeRateLimitError
from utils.security import generate_shopee_auth_headers

log = logging.getLogger(__name__)

class ShopeeGraphQLClient:
    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._endpoint = "https://open-api.affiliate.shopee.com.br/graphql"
        
        # Connection Pooling e Keep-Alive para máxima performance Server-to-Server
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        self._client = httpx.AsyncClient(limits=limits, timeout=15.0)

    async def execute_query(self, query: str, variables: dict) -> dict:
        """
        Executa uma requisição GraphQL com proteção de Retries e Backoff Exponencial.
        """
        payload = {"query": query, "variables": variables}
        headers, payload_str = generate_shopee_auth_headers(
            self._app_id, self._app_secret, payload
        )

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                # Dispara a requisição enviando a string exata usada no HMAC
                response = await self._client.post(
                    self._endpoint, 
                    content=payload_str, 
                    headers=headers
                )
                
                # Tratamento de Rate Limit da Camada HTTP
                if response.status_code == 429:
                    raise ShopeeRateLimitError(retry_after=60)
                    
                response.raise_for_status()
                data = response.json()
                
                # Tratamento de Erros da Camada GraphQL
                if "errors" in data:
                    err_msg = data["errors"][0].get("message", "Unknown GraphQL Error")
                    if "auth" in err_msg.lower() or "signature" in err_msg.lower():
                        raise ShopeeAuthError(f"Erro de Autenticação Shopee: {err_msg}")
                    log.error(f"[SHOPEE_GRAPHQL_ERROR] {err_msg}")
                    # Retorna os dados mesmo com erros, o parser lida com as faltas
                
                return data

            except httpx.RequestError as e:
                log.warning(f"[SHOPEE_NETWORK_ERROR] Falha na tentativa {attempt}/{max_retries}: {e}")
                if attempt == max_retries:
                    raise ShopeeAPIError("Falha ao comunicar com a Shopee após múltiplos retries") from e
                
                # Backoff Exponencial: 2s, 4s, 8s
                await asyncio.sleep(2 ** attempt)

            except ShopeeRateLimitError as e:
                log.warning(f"[SHOPEE_RATE_LIMIT] Bloqueado. Aguardando {e.retry_after}s")
                await asyncio.sleep(e.retry_after)

    async def close(self):
        """Limpa as conexões do pool no Graceful Shutdown"""
        await self._client.aclose()