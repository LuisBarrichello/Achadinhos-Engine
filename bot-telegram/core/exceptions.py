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