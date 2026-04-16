import logging

import httpx

from models.deal import Deal

log = logging.getLogger("garimpeiro")


class VitrineAPI:
    def __init__(self, base_url: str, admin_secret: str, timeout: float) -> None:
        self._base    = base_url
        self._timeout = timeout
        self._headers = {
            "Content-Type"  : "application/json",
            "x-admin-secret": admin_secret,
        }

    async def publish(self, deal: Deal) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/links",
                    json=deal.to_vitrine_payload(),
                    headers=self._headers,
                )
            if resp.status_code == 403:
                log.error("Vitrine: ADMIN_SECRET inválido (403)")
                return False
            resp.raise_for_status()
            log.info(f"Vitrine: link criado — '{deal.title[:50]}'")
            return True
        except Exception as e:
            log.warning(f"Vitrine: {e}")
            return False
