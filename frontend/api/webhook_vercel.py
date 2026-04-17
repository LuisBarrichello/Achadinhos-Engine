"""
frontend/api/webhook.py — Vercel Serverless Function

[FIX-6.1] _resolve_dm_message agora usa keyword_matches de shared/matching.py
           em vez de reimplementar a lógica inline.

Nota de deploy: inclua shared/matching.py no bundle da Vercel via vercel.json:
  "includeFiles": ["../shared/**"]
"""

from http.server import BaseHTTPRequestHandler
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlparse, parse_qs

_CORS = {
    "Access-Control-Allow-Origin" : "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, x-hub-signature-256",
}

WEBHOOK_VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN", "")
META_APP_SECRET      = os.environ.get("META_APP_SECRET", "")
DATABASE_URL         = os.environ.get("DATABASE_URL", "")


# [FIX-6.1] Importa do módulo compartilhado
try:
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
    from shared.matching import keyword_matches
except ImportError:
    # Fallback inline para o caso de deploy sem o shared no path
    import re, unicodedata

    def _norm(text: str) -> str:
        nfd = unicodedata.normalize("NFD", text)
        a = nfd.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^A-Z0-9]", "", a.upper())

    def keyword_matches(comment: str, keyword: str) -> bool:
        return bool(keyword) and _norm(keyword) in _norm(comment)


# ── DB helpers ────────────────────────────────────────────────────────────

def _get_conn():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


def _resolve_dm_message(raw_text: str) -> str | None:
    if not DATABASE_URL:
        return None

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # 1. Links com keyword inline
            cur.execute(
                'SELECT keyword, url FROM link WHERE keyword IS NOT NULL AND active = true'
            )
            for kw, url in cur.fetchall():
                if keyword_matches(raw_text, kw):  # [FIX-6.1]
                    return (
                        f"Oi! Obrigado pelo interesse! 🛍️\n"
                        f"Aqui está o link do produto:\n{url}"
                    )

            # 2. Tabela legada KeywordLink
            cur.execute('SELECT keyword, url, message FROM keywordlink')
            for kw, url, message in cur.fetchall():
                if keyword_matches(raw_text, kw):  # [FIX-6.1]
                    return message.format(url=url)

    finally:
        conn.close()

    return None


def _enqueue_dm(user_id: str, message: str) -> None:
    if not DATABASE_URL:
        return
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO webhook_events (user_id, message, status, created_at) "
                "VALUES (%s, %s, 'pending', %s)",
                (user_id, message, int(time.time())),
            )
        conn.commit()
    finally:
        conn.close()


def _process_payload(payload: dict) -> int:
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
        length     = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(length)

        if META_APP_SECRET:
            sig      = self.headers.get("x-hub-signature-256", "")
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
            self._write_json({"status": "ok", "warning": str(exc)[:200]})
            return

        self._write_json({"status": "ok", "enqueued": enqueued})

    def log_message(self, *args):
        pass
