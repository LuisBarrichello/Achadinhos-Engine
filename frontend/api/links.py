"""
frontend/api/links.py — Vercel Serverless Function
====================================================
Conecta DIRETAMENTE ao PostgreSQL (Supabase/Neon/Render PG)
e serve GET /api/links sem passar pelo FastAPI no Render.

Benefícios:
  - Vitrine sobrevive a restarts/hibernate do Render
  - Latência menor (edge → DB direto, sem hop extra)
  - Escala para milhões de req/mês no Free Tier da Vercel

Deploy:
  1. Adicione DATABASE_URL nas env vars do projeto Vercel
  2. Faça deploy normalmente — a Vercel detecta api/*.py
     e cria serverless functions automaticamente.

Requisitos (frontend/api/requirements.txt):
  psycopg2-binary==2.9.9
"""

from http.server import BaseHTTPRequestHandler
import json
import os

_CORS = {
    "Access-Control-Allow-Origin" : "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

_COLS = [
    "id", "title", "url", "emoji", "badge",
    "badge_color", "active", "order", "clicks",
    "image_url", "keyword",
]

_QUERY = (
    'SELECT id, title, url, emoji, badge, badge_color, active, '
    '"order", clicks, image_url, keyword '
    'FROM link WHERE active = true ORDER BY "order" ASC'
)


def _get_links() -> list[dict]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL não configurada nas env vars da Vercel")

    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(_QUERY)
            rows = cur.fetchall()
            return [dict(row) for row in rows]
    finally:
        conn.close()


class handler(BaseHTTPRequestHandler):

    def _send_headers(self, status: int, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for k, v in _CORS.items():
            self.send_header(k, v)
        self.end_headers()

    def _write_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
        self._send_headers(status)
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_headers(200)

    def do_GET(self):
        try:
            links = _get_links()
            self._write_json(links)
        except Exception as exc:
            self._write_json({"error": str(exc)}, status=500)

    def log_message(self, *args):
        pass  # Vercel captura logs pelo stdout; silencia o stderr padrão
