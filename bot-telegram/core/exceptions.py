class AchadinhosError(Exception):
    """Erro base do sistema"""
    pass

class TelegramNetworkError(AchadinhosError):
    """Falha de rede temporária (Retry seguro)"""
    pass

class TelegramRateLimitError(AchadinhosError):
    """Flood control ativado pelo Telegram"""
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Rate limit. Retry after {retry_after}s")

class FatalSystemError(AchadinhosError):
    """Erro irrecuperável (Abortar)"""
    pass

class ShopeeAPIError(AchadinhosError):
    """Erro base da integração Shopee"""
    pass

class ShopeeAuthError(ShopeeAPIError):
    """Assinatura HMAC inválida ou App ID incorreto"""
    pass

class ShopeeRateLimitError(ShopeeAPIError):
    """Quota excedida na API GraphQL da Shopee"""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Shopee Rate Limit. Retry after {retry_after}s")