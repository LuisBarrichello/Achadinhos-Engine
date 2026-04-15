"""
video-maker/local_video_worker.py
===================================
Worker LOCAL para geração de vídeos.
Roda na sua máquina (GPU/CPU disponíveis), não no Render.

Fluxo:
  1. Busca links recentes via Admin API  →  GET /links
  2. Gera roteiro (Claude Haiku)         →  script_generator.py
  3. Sintetiza áudio (Kokoro/Fish)       →  tts_client.py
  4. Monta vídeo 9:16 (FFmpeg)           →  video_assembler.py
  5. Salva em VIDEO_OUTPUT_DIR           →  para o gui_organizer exportar

Uso:
  python local_video_worker.py                 # processa todos os links ativos
  python local_video_worker.py --limit 3       # processa só os 3 primeiros
  python local_video_worker.py --id 42         # processa link específico
  python local_video_worker.py --dry-run       # simula sem gerar vídeo

Env vars (herda do .env):
  API_BASE_URL   — URL do backend FastAPI (ex: https://achadinhos-api.onrender.com)
  ADMIN_SECRET   — para autenticar na API
  VIDEO_OUTPUT_DIR — pasta de saída (padrão: ./output_videos)
  TTS_ENGINE, KOKORO_*, FISH_* — configurados em tts_client.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("local_video_worker")

# ── Config ────────────────────────────────────────────────────────────────
API_BASE_URL   = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
ADMIN_SECRET   = os.getenv("ADMIN_SECRET", "")
OUTPUT_DIR     = Path(os.getenv("VIDEO_OUTPUT_DIR", "./output_videos"))
HTTP_TIMEOUT   = float(os.getenv("HTTP_TIMEOUT", "20.0"))
WORK_DIR       = Path("./temp_videos")


# ── Modelo mínimo (espelha Deal do garimpeiro sem dependência dele) ────────
@dataclass
class LinkItem:
    id          : int
    title       : str
    url         : str
    image_url   : Optional[str]

    # Campos opcionais para script_generator (podem ser None se não disponíveis)
    price           : Optional[float] = None
    original_price  : Optional[float] = None
    discount_pct    : Optional[int]   = None
    affiliate_url   : str             = ""

    def __post_init__(self):
        if not self.affiliate_url:
            self.affiliate_url = self.url

    @property
    def item_id(self) -> str:
        return str(self.id)


# ── API helpers ────────────────────────────────────────────────────────────
async def fetch_links(limit: int = 0, link_id: Optional[int] = None) -> list[LinkItem]:
    headers = {"x-admin-secret": ADMIN_SECRET}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(f"{API_BASE_URL}/links", headers=headers)
        resp.raise_for_status()
        data = resp.json()

    items = []
    for row in data:
        if link_id and row["id"] != link_id:
            continue
        items.append(LinkItem(
            id        = row["id"],
            title     = row["title"],
            url       = row["url"],
            image_url = row.get("image_url"),
        ))
        if limit and len(items) >= limit:
            break

    return items


# ── Pipeline de vídeo ──────────────────────────────────────────────────────
async def process_link(link: LinkItem, dry_run: bool = False) -> bool:
    """
    Gera vídeo para um link. Retorna True em sucesso.
    Falhas são logadas e não interrompem outros links.
    """
    log.info(f"─── [{link.id}] {link.title[:60]}")

    audio_path  = WORK_DIR / f"{link.item_id}_audio.wav"
    video_path  = OUTPUT_DIR / f"{link.item_id}_final.mp4"

    if video_path.exists():
        log.info(f"  ⏭  Vídeo já existe: {video_path.name}")
        return True

    if dry_run:
        log.info(f"  [DRY-RUN] Geraria: {video_path}")
        return True

    # ── 1. Roteiro ──────────────────────────────────────────────────────
    try:
        from script_generator import generate_script, script_to_narration
        sections  = await generate_script(link)
        narration = script_to_narration(sections)
        log.info(f"  📝 Roteiro: {narration[:80]}…")
    except Exception as exc:
        log.error(f"  ✗ Roteiro falhou: {exc}")
        return False

    # ── 2. TTS ─────────────────────────────────────────────────────────
    try:
        from tts_client import synthesize
        tts_ok = await synthesize(narration, audio_path)
        if not tts_ok:
            log.error("  ✗ TTS falhou")
            return False
        log.info(f"  🎙  Áudio gerado: {audio_path.name}")
    except Exception as exc:
        log.error(f"  ✗ TTS exceção: {exc}")
        return False

    # ── 3. Vídeo ────────────────────────────────────────────────────────
    try:
        from video_assembler import assemble_video, download_image, VideoAssemblerError

        img_path = None
        if link.image_url:
            img_path = WORK_DIR / f"{link.item_id}_image.jpg"
            ok = await download_image(link.image_url, img_path)
            if not ok:
                img_path = None

        if not img_path or not img_path.exists():
            log.warning("  ⚠  Sem imagem — vídeo não gerado")
            return False

        success, reason = await assemble_video(
            image_path  = img_path,
            audio_path  = audio_path,
            output_path = video_path,
            title       = link.title,
            cta         = "Clique no link da bio!",
        )

        if success:
            log.info(f"  ✅ Vídeo: {video_path}")
            return True
        else:
            log.error(f"  ✗ Montagem falhou: {reason}")
            return False

    except Exception as exc:
        log.error(f"  ✗ Vídeo exceção: {exc}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────
async def run(limit: int = 0, link_id: Optional[int] = None, dry_run: bool = False):
    WORK_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("Local Video Worker")
    log.info(f"  API        : {API_BASE_URL}")
    log.info(f"  Output     : {OUTPUT_DIR}")
    log.info(f"  Dry run    : {dry_run}")
    log.info("=" * 60)

    links = await fetch_links(limit=limit, link_id=link_id)
    if not links:
        log.warning("Nenhum link retornado pela API.")
        return

    log.info(f"📦 {len(links)} link(s) a processar\n")

    ok = failed = 0
    for link in links:
        success = await process_link(link, dry_run=dry_run)
        if success:
            ok += 1
        else:
            failed += 1
        await asyncio.sleep(0.5)

    log.info(f"\n📊 Concluído — ✅ {ok} | ✗ {failed} | Total {len(links)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local Video Worker — Achadinhos")
    parser.add_argument("--limit",   type=int, default=0,    help="Máx de links (0 = todos)")
    parser.add_argument("--id",      type=int, default=None, help="Processar link específico por ID")
    parser.add_argument("--dry-run", action="store_true",    help="Simula sem gerar vídeo")
    args = parser.parse_args()

    asyncio.run(run(limit=args.limit, link_id=args.id, dry_run=args.dry_run))
