import logging
from psycopg2 import pool

log = logging.getLogger(__name__)


class DealStore:
    def __init__(self, db_url: str):
        self._pool = pool.SimpleConnectionPool(1, 5, db_url)
        log.info("DealStore conectado ao PostgreSQL.")

    def is_processed(self, item_id: str) -> bool:
        """Verifica se o item já existe no banco."""
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM processed_deals WHERE item_id = %s LIMIT 1;", (item_id,))
                result = cur.fetchone()
                return result is not None
        finally:
            self._pool.putconn(conn)

    def mark_processed(self, item_id: str) -> None:
        """Marca como processado ignorando se já existir."""
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                query = """
                    INSERT INTO processed_deals (item_id, processed_at)
                    VALUES (%s, NOW())
                    ON CONFLICT (item_id) DO NOTHING;
                """
                cur.execute(query, (item_id,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"Erro ao salvar deal {item_id}: {e}")
        finally:
            self._pool.putconn(conn)