import json
from psycopg2 import pool
from psycopg2.extras import Json
from models.deal import Deal
from core.logger import setup_logger

log = setup_logger(__name__)

class DealStore:
    def __init__(self, db_url: str):
        self._pool = pool.SimpleConnectionPool(1, 5, db_url)
        log.info("DealStore conectado ao PostgreSQL [Ready]")

    def is_processed(self, fingerprint: str) -> bool:
        """Agora checa pelo Fingerprint em vez do item_id"""
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM processed_deals WHERE fingerprint = %s LIMIT 1", (fingerprint,))
                return cur.fetchone() is not None
        finally:
            self._pool.putconn(conn)

    def save_deal(self, deal: Deal) -> None:
        """Salva usando o fingerprint como chave primária"""
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                # Usa o novo método to_dict() criado no Passo 2
                payload = deal.to_dict()

                query = """
                    INSERT INTO processed_deals (fingerprint, item_id, processed_at, payload)
                    VALUES (%s, %s, NOW(), %s)
                    ON CONFLICT (fingerprint) DO UPDATE 
                    SET processed_at = NOW(), payload = EXCLUDED.payload;
                """
                cur.execute(query, (deal.fingerprint, deal.item_id, Json(payload)))
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"[DB_ERROR] Falha ao salvar deal {deal.item_id}: {e}")
        finally:
            self._pool.putconn(conn)

    def get_candidates_for_repost(self, days_old: int = 7, limit: int = 5) -> list[Deal]:
        """Busca no banco ofertas antigas que podem ser repostadas."""
        conn = self._pool.getconn()
        deals = []
        try:
            with conn.cursor() as cur:
                query = """
                    SELECT payload FROM processed_deals
                    WHERE processed_at < NOW() - INTERVAL '%s days'
                    AND payload IS NOT NULL
                    ORDER BY RANDOM() LIMIT %s;
                """
                cur.execute(query, (days_old, limit))
                for row in cur.fetchall():
                    try:
                        deals.append(Deal(**row[0]))
                    except Exception as e:
                        log.warning(f"Falha ao desserializar deal antigo: {e}")
        finally:
            self._pool.putconn(conn)
        return deals