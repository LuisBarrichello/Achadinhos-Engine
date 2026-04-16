import hmac
import time

from fastapi import Header, HTTPException, Request

from core.config import ADMIN_SECRET

# ── Rate limiting de cliques (cache in-memory) ────────────────────────────────
_click_cache: dict[str, float] = {}
CLICK_COOLDOWN_SECONDS     = 60
_CLICK_CACHE_CLEANUP_EVERY = 500
_click_cache_inserts       = 0


def verify_admin(x_admin_secret: str = Header(...)) -> None:
    if not hmac.compare_digest(x_admin_secret.encode(), ADMIN_SECRET.encode()):
        raise HTTPException(status_code=403, detail="Não autorizado")


def _rate_limit_click(request: Request, link_id: int) -> bool:
    global _click_cache_inserts
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{link_id}"
    now = time.monotonic()
    if now - _click_cache.get(key, 0.0) < CLICK_COOLDOWN_SECONDS:
        return False
    _click_cache[key] = now
    _click_cache_inserts += 1
    if _click_cache_inserts >= _CLICK_CACHE_CLEANUP_EVERY:
        cutoff  = now - CLICK_COOLDOWN_SECONDS
        expired = [k for k, ts in _click_cache.items() if ts < cutoff]
        for k in expired:
            del _click_cache[k]
        _click_cache_inserts = 0
    return True
