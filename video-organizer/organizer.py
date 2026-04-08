"""
=============================================================
 Achadinhos do Momento — Video Organizer
 Lê queue.csv e move/renomeia vídeos para pasta organizada
=============================================================
Estrutura do queue.csv:
  video_path, title, store, badge, url

Uso:
  python organizer.py                  # processa queue.csv
  python organizer.py --dry-run        # apenas simula
  python organizer.py --csv outro.csv  # arquivo alternativo
=============================================================
"""

import argparse
import csv
import logging
import os
import shutil
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("organizer")

OUTPUT_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", "./organized"))
QUEUE_CSV  = Path(os.getenv("QUEUE_CSV", "./queue.csv"))


def slugify(text: str) -> str:
    import re, unicodedata
    text = unicodedata.normalize("NFD", text)
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "_", text).strip("_")


def process_queue(csv_path: Path, dry_run: bool = False):
    if not csv_path.exists():
        log.error(f"Arquivo não encontrado: {csv_path}")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    processed = 0
    skipped   = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows   = list(reader)

    log.info(f"📋 {len(rows)} vídeo(s) na fila")

    done_rows = []
    for row in rows:
        src_path = Path(row.get("video_path", "").strip())
        title    = row.get("title", "sem_titulo").strip()
        store    = row.get("store", "outros").strip().lower()
        badge    = row.get("badge", "").strip()
        status   = row.get("status", "pending").strip()

        if status == "done":
            done_rows.append({**row, "status": "done"})
            skipped += 1
            continue

        if not src_path.exists():
            log.warning(f"⚠️  Arquivo não encontrado: {src_path} — pulando")
            done_rows.append({**row, "status": "missing"})
            skipped += 1
            continue

        # Destino: organized/<store>/<data>_<slug>.<ext>
        store_dir = OUTPUT_DIR / store
        store_dir.mkdir(parents=True, exist_ok=True)
        slug     = slugify(title)
        dest     = store_dir / f"{today}_{slug}{src_path.suffix}"

        if dry_run:
            log.info(f"[DRY-RUN] {src_path} → {dest}")
        else:
            shutil.copy2(src_path, dest)
            log.info(f"✅ Copiado: {dest.name}")

        processed += 1
        done_rows.append({**row, "status": "done"})

    # Reescrever CSV com status atualizado
    if not dry_run and rows:
        fieldnames = list(rows[0].keys())
        if "status" not in fieldnames:
            fieldnames.append("status")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(done_rows)

    log.info(f"\n📊 Resultado: {processed} processado(s), {skipped} ignorado(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Video Organizer para Achadinhos")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--csv",     default=str(QUEUE_CSV))
    args = parser.parse_args()
    process_queue(Path(args.csv), dry_run=args.dry_run)
