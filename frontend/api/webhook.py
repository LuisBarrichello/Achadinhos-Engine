"""
frontend/api/webhook.py — Vercel Serverless Function
=====================================================
Recebe eventos do Instagram/Meta e salva na tabela
webhook_events do Neon diretamente.

Benefícios vs. Render FastAPI:
  - Resposta em < 50ms (sem cold start)
  - Meta nunca desabilita o webhook por timeout
  - Render FastAPI pode hibernar sem impacto

Env vars necessárias (Vercel):
  DATABASE_URL          — PostgreSQL (Neon/Supabase)
  WEBHOOK_VERIFY_TOKEN  — token de verificação da Meta
  META_APP_SECRET       — app secret para validar assinatura (opcional)
"""

from http.server import BaseHTTPRequestHandler
import hashlib
import hmac
import json
import os
import re
import time
import unicodedata
from urllib.parse import urlparse, parse_qs

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, x-hub-signature-256",
}

WEBHOOK_VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN", "")
META_APP_SECRET      = os.environ.get("META_APP_SECRET", "")
DATABASE_URL         = os.environ.get("DATABASE_URL", "")


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
        """Recebe evento Meta e enfileira DMs."""
        length     = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(length)

        # Valida assinatura (opcional mas recomendado)
        if META_APP_SECRET:
            sig = self.headers.get("x-hub-signature-256", "")
            expected = "sha256=" + hmac.new(
                META_APP_SECRET.encode(), body_bytes, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig, expected):
                self._write_json({"error": "Assinatura inválida"}, 403)
                return

        try:
            payload = json.loads(body_bytes)
        except json.JSONDecodeError:
            self._write_json({"error": "Payload inválido"}, 400)
            return

        try:
            enqueued = _process_payload(payload)
        except Exception as exc:
            # Retorna 200 mesmo em erro interno — Meta não deve punir falha de DB
            self._write_json({"status": "ok", "warning": str(exc)[:200]})
            return

        self._write_json({"status": "ok", "enqueued": enqueued})

    def log_message(self, *args):
        pass
