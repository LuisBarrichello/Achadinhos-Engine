import asyncio
import httpx
import logging
from psycopg2 import pool

log = logging.getLogger(__name__)


class DMProcessor:
    def __init__(self, db_url: str, pat: str):
        # Pool local persistente para o Worker (mínimo 1, máximo 5 conexões)
        self._pool = pool.SimpleConnectionPool(1, 5, db_url)
        self._pat = pat
        self.GRAPH_DM_URL = "https://graph.facebook.com/v19.0/me/messages"

    async def start_loop(self):
        """Loop infinito de processamento de DMs"""
        log.info("Iniciando Worker de DMs (SKIP LOCKED mode)...")
        while True:
            try:
                await self._process_next_pending()
                # Pausa curta para não fritar a CPU, ajustável conforme o volume
                await asyncio.sleep(2)
            except Exception as e:
                log.error(f"Erro crítico no loop de DMs: {e}")
                await asyncio.sleep(5)  # Backoff em caso de falha do DB

    async def _process_next_pending(self):
        conn = self._pool.getconn()
        task = None
        try:
            with conn.cursor() as cur:
                # O CORAÇÃO DA CONCORRÊNCIA: Trava atômica no banco de dados
                query = """
                    UPDATE webhook_events
                    SET status = 'processing', processed_at = NOW()
                    WHERE id = (
                        SELECT id FROM webhook_events
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id, user_id, message;
                """
                cur.execute(query)
                task = cur.fetchone()
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"Erro ao buscar tarefa: {e}")
        finally:
            self._pool.putconn(conn)

        # Se não há tarefas, sai silenciosamente
        if not task:
            return

        task_id, user_id, message = task
        log.info(f"Processando DM Task {task_id} para usuário {user_id}")

        # Envia a DM via Meta Graph API
        success = await self._send_instagram_dm(user_id, message)

        # Atualiza status final
        self._finalize_task(task_id, success)

    async def _send_instagram_dm(self, user_id: str, message: str) -> bool:
        # Aqui você implementa sua lógica de matching de Keyword
        # Exemplo simplificado focado em infraestrutura:
        payload = {
            "recipient": {"id": user_id},
            "message": {"text": "Aqui está o seu link: https://seusite.com"}
        }
        headers = {
            "Authorization": f"Bearer {self._pat}",  # CORREÇÃO: Token no Header, não na URL!
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(self.GRAPH_DM_URL, json=payload, headers=headers)
            if resp.status_code == 200:
                return True
            else:
                log.error(f"Falha na Meta API: {resp.text}")
                return False

    def _finalize_task(self, task_id: int, success: bool):
        conn = self._pool.getconn()
        try:
            status = 'completed' if success else 'failed'
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE webhook_events SET status = %s WHERE id = %s",
                    (status, task_id)
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"Erro ao finalizar task {task_id}: {e}")
        finally:
            self._pool.putconn(conn)