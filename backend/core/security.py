import hmac
import time
from typing import Dict, Tuple
from fastapi import HTTPException, status, Request
from core.config import ADMIN_SECRET


# Token Bucket Rate Limiter na memória (Simples e eficaz para APIs pequenas)
class BruteForceProtector:
    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.clients: Dict[str, Tuple[int, float]] = {}

    def is_rate_limited(self, ip: str) -> bool:
        current_time = time.time()
        requests, window_start = self.clients.get(ip, (0, current_time))

        if current_time - window_start > self.window_seconds:
            # Reseta a janela
            self.clients[ip] = (1, current_time)
            return False

        if requests >= self.max_requests:
            return True

        self.clients[ip] = (requests + 1, window_start)
        return False


# Instância global do protetor do Admin
admin_protector = BruteForceProtector(max_requests=5, window_seconds=60)


def verify_admin_secure(request: Request, x_admin_secret: str):
    client_ip = request.client.host

    # 1. Proteção contra Brute Force
    if admin_protector.is_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Please try again later."
        )

    # 2. Proteção contra Timing Attacks usando hmac.compare_digest
    if not hmac.compare_digest(x_admin_secret.encode('utf-8'), ADMIN_SECRET.encode('utf-8')):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Admin Secret"
        )

    return True