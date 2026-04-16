import asyncio
import logging
import time
from typing import Optional

import httpx

log = logging.getLogger("garimpeiro")


class DMProcessor:
    """
    [V5-2] Drena a tabela webhook_events diretamente via psycopg2.

    Elimina o round-trip HTTP:
      ANTES: garimpeiro → HTTP → FastAPI → Neon
      AGORA: garimpeiro → psycopg2 → Neon (direto)

    Mesmo banco, sem hop extra. Latência ~10ms vs ~200ms.
    """

    GRAPH_DM_URL = "https://graph.facebook.com/v19.0/me/messages"

    def __init__(
        self,
        database_url      : str,
        page_access_token : str,
        timeout           : float,
    ) -> None:
        self._database_url = database_url
        self._pat          = page_access_token
        self._timeout      = timeout

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _get_conn(self):
        import psycopg2
        return psycopg2.connect(self._database_url)

    def _fetch_pending(self, limit: int = 50) -> list[dict]:
        """Lê eventos pendentes diretamente do Neon."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, message
                    FROM webhook_events
                    WHERE status = 'pending'
                    ORDER BY created_at
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
            return [{"id": r[0], "user_id": r[1], "message": r[2]} for r in rows]
        finally:
            conn.close()

    def _update_status(
        self,
        event_id : int,
        status   : str,
        error    : Optional[str] = None,
    ) -> None:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                if status in ("completed", "failed"):
                    cur.execute(
                        """
                        UPDATE webhook_events
                        SET status=%s, processed_at=%s, error=%s
                        WHERE id=%s
                        """,
                        (status, int(time.time()), error[:500] if error else None, event_id),
                    )
                else:
                    cur.execute(
                        "UPDATE webhook_events SET status=%s WHERE id=%s",
                        (status, event_id),
                    )
            conn.commit()
        finally:
            conn.close()

    # ── Envio Graph API ───────────────────────────────────────────────────────

    async def _send_dm(self, recipient_id: str, message: str) -> tuple[bool, str | None]:
        if not self._pat:
            return False, "PAGE_ACCESS_TOKEN não configurado"

        payload = {
            "recipient"      : {"id": recipient_id},
            "message"        : {"text": message},
            "messaging_type" : "RESPONSE",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self.GRAPH_DM_URL,
                    json=payload,
                    params={"access_token": self._pat},
                )
            if resp.status_code == 200:
                return True, None
            return False, f"Graph API {resp.status_code}: {resp.text[:150]}"
        except httpx.TimeoutException:
            return False, "Timeout na Graph API"
        except Exception as exc:
            return False, f"Exceção: {exc}"

    # ── Processamento ─────────────────────────────────────────────────────────

    async def process_pending(self) -> int:
        """[V5-2] Lê Neon diretamente e envia DMs. Retorna DMs enviadas."""
        try:
            events = self._fetch_pending()
        except Exception as exc:
            log.warning(f"DMProcessor: falha ao buscar fila: {exc}")
            return 0

        if not events:
            return 0

        log.info(f"📨 DMProcessor: {len(events)} DM(s) na fila")
        sent = 0

        for event in events:
            event_id = event["id"]
            user_id  = event["user_id"]
            message  = event["message"]

            try:
                self._update_status(event_id, "processing")
            except Exception as exc:
                log.warning(f"  status→processing falhou: {exc}")

            ok, reason = await self._send_dm(user_id, message)

            try:
                if ok:
                    self._update_status(event_id, "completed")
                    log.info(f"  ✅ DM enviada → {user_id}")
                    sent += 1
                else:
                    self._update_status(event_id, "failed", reason)
                    log.warning(f"  ❌ DM falhou → {user_id} | {reason}")
            except Exception as exc:
                log.warning(f"  status→final falhou: {exc}")

            await asyncio.sleep(0.5)

        return sent
