from http.server import BaseHTTPRequestHandler
import hashlib
import hmac
import json
import os
import re
import time
import unicodedata
import psycopg2
from urllib.parse import urlparse, parse_qs

DB_URL = os.getenv("DATABASE_URL")

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, x-hub-signature-256",
}

WEBHOOK_VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN", "")
META_APP_SECRET      = os.environ.get("META_APP_SECRET", "")
DATABASE_URL         = os.environ.get("DATABASE_URL", "")
MAX_PAYLOAD_SIZE = 1024 * 100

# ── Normalização de keyword (espelho do main.py) ──────────────────────────
def _normalize_keyword(text: str) -> str:
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", text)
    ascii_text = nfd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]", "", ascii_text.upper())


def _keyword_matches(comment: str, keyword: str) -> bool:
    norm_comment = _normalize_keyword(comment)
    norm_keyword = _normalize_keyword(keyword)
    if not norm_keyword:
        return False
    return norm_keyword in norm_comment


# ── DB helpers ────────────────────────────────────────────────────────────
def _get_conn():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


def _resolve_dm_message(raw_text: str) -> str | None:
    """
    Mesma lógica do main.py/_resolve_dm_message, mas via psycopg2 direto.
    Evita dependência do FastAPI / SQLModel nesta função serverless.
    """
    if not DATABASE_URL:
        return None

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # 1. Links com keyword inline
            cur.execute(
                'SELECT keyword, url FROM link WHERE keyword IS NOT NULL AND active = true'
            )
            for keyword, url in cur.fetchall():
                if _keyword_matches(raw_text, keyword):
                    return (
                        f"Oi! Obrigado pelo interesse! 🛍️\n"
                        f"Aqui está o link do produto:\n{url}"
                    )

            # 2. Tabela legada KeywordLink
            cur.execute('SELECT keyword, url, message FROM keywordlink')
            for keyword, url, message in cur.fetchall():
                if _keyword_matches(raw_text, keyword):
                    return message.format(url=url)

    finally:
        conn.close()

    return None


def _enqueue_dm(user_id: str, message: str) -> None:
    """Salva DM pendente na fila do banco."""
    if not DATABASE_URL:
        return

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO webhook_events (user_id, message, status, created_at)
                VALUES (%s, %s, 'pending', %s)
                """,
                (user_id, message, int(time.time())),
            )
        conn.commit()
    finally:
        conn.close()


def _process_payload(payload: dict) -> int:
    """Processa payload Meta e enfileira DMs. Retorna quantidade enfileirada."""
    enqueued = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue

            value    = change.get("value", {})
            raw_text = value.get("text", "")
            user_id  = value.get("from", {}).get("id")

            if not user_id or not raw_text:
                continue

            message = _resolve_dm_message(raw_text)
            if message:
                _enqueue_dm(user_id, message)
                enqueued += 1

    return enqueued


# ── Handler Vercel ────────────────────────────────────────────────────────
class handler(BaseHTTPRequestHandler):

    def _send_headers(self, status: int, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for k, v in _CORS.items():
            self.send_header(k, v)
        self.end_headers()

    def _write_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_headers(status)
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_headers(200)

    def do_GET(self):
        """Verificação do webhook Meta."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        mode      = params.get("hub.mode",         [None])[0]
        challenge = params.get("hub.challenge",    [None])[0]
        token     = params.get("hub.verify_token", [None])[0]

        if not WEBHOOK_VERIFY_TOKEN:
            self._write_json({"error": "WEBHOOK_VERIFY_TOKEN não configurado"}, 500)
            return

        token_ok = token and hmac.compare_digest(
            token.encode(), WEBHOOK_VERIFY_TOKEN.encode()
        )

        if mode == "subscribe" and token_ok and challenge:
            self._send_headers(200, "text/plain")
            self.wfile.write(challenge.encode())
            return

        self._write_json({"error": "Token inválido"}, 403)

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))

        # Proteção: Rejeitar payloads maliciosamente grandes
        if content_length > MAX_PAYLOAD_SIZE:
            self._send_response(413, "Payload Too Large")
            return

        post_data = self.rfile.read(content_length)
        signature = self.headers.get('x-hub-signature-256', '')

        # Proteção P0: Spoofing & Integridade
        if not self._verify_meta_signature(post_data, signature):
            self._send_response(401, "Unauthorized: Invalid Signature")
            return

        try:
            payload = json.loads(post_data.decode('utf-8'))
            entry = payload.get("entry", [])[0]
            change = entry.get("changes", [])[0]
            value = change.get("value", {})

            comment_id = value.get("id")  # Funciona como Nonce/Idempotency Key
            user_id = value.get("from", {}).get("id")
            message = value.get("text", "")

            if not comment_id or not user_id:
                self._send_response(200, "Ignored: Missing data")
                return

            self._enqueue_dm(user_id, comment_id, message)
            self._send_response(200, "OK")

        except Exception as e:
            # Nunca exponha o erro real pro mundo exterior
            print(f"Erro processando webhook: {e}")
            self._send_response(200, "Processed with internal error")

    def _enqueue_dm(self, user_id: str, external_event_id: str, message: str) -> None:
        """
        Conexão Serverless-Safe: Abre, executa com idempotência, e fecha.
        """
        conn = None
        try:
            conn = psycopg2.connect(DB_URL)
            with conn.cursor() as cur:
                # O Segredo da Idempotência: ON CONFLICT DO NOTHING
                query = """
                    INSERT INTO webhook_events (user_id, external_event_id, message, status, created_at)
                    VALUES (%s, %s, %s, 'pending', NOW())
                    ON CONFLICT (external_event_id) DO NOTHING;
                """
                cur.execute(query, (user_id, external_event_id, message))
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            raise e
        finally:
            if conn:
                conn.close()

    def _send_response(self, code: int, text: str):
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"status": text}).encode('utf-8'))

    def log_message(self, *args):
        pass

    def _verify_meta_signature(self, payload_body: bytes, signature_header: str) -> bool:
        """Proteção P0: Valida se o request realmente veio dos servidores da Meta."""
        if not META_APP_SECRET or not signature_header:
            return False

        # O header vem no formato: "sha256=HASH..."
        if not signature_header.startswith("sha256="):
            return False

        expected_hash = signature_header.split("sha256=")[1]

        # Calcula o HMAC-SHA256 do body raw usando o App Secret
        calculated_hash = hmac.new(
            META_APP_SECRET.encode('utf-8'),
            payload_body,
            hashlib.sha256
        ).hexdigest()

        # Comparação constante de tempo (Previne Timing Attacks)
        return hmac.compare_digest(expected_hash, calculated_hash)