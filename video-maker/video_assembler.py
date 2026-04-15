"""
video_assembler.py — Montagem de vídeo 9:16 com guard de memória.

Proteções adicionadas:
  [OOM-1] Verifica RAM disponível antes de montar (mínimo configurável).
  [OOM-2] Captura MemoryError e erros de subprocess (código 137 = OOM kill).
  [OOM-3] Expõe VideoAssemblerError para que garimpeiro.py possa reagir.
  [OOM-4] assemble_video devolve (bool, str | None) — bool=sucesso, str=motivo da falha.
"""

import asyncio
import os
import logging
from pathlib import Path

import httpx

log = logging.getLogger("video_assembler")

WORK_DIR = Path("./temp_videos")
WORK_DIR.mkdir(exist_ok=True)

# Mínimo de RAM livre (MB) antes de tentar montar o vídeo.
# No Render Free (~512 MB total), 200 MB é um limite seguro.
MIN_FREE_RAM_MB: int = int(os.getenv("VIDEO_MIN_FREE_RAM_MB", "200"))


# ─── Exceção pública ──────────────────────────────────────────
class VideoAssemblerError(Exception):
    """Lançada quando a montagem falha por razão conhecida."""


# ─── Helpers ──────────────────────────────────────────────────

def _free_ram_mb() -> float:
    """Retorna RAM livre em MB. Usa psutil se disponível; senão lê /proc/meminfo."""
    try:
        import psutil
        return psutil.virtual_memory().available / 1_048_576
    except ImportError:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return float("inf")   # não conseguiu medir → deixa tentar


def _check_ram() -> None:
    """[OOM-1] Lança VideoAssemblerError se RAM livre < MIN_FREE_RAM_MB."""
    free = _free_ram_mb()
    if free < MIN_FREE_RAM_MB:
        raise VideoAssemblerError(
            f"RAM insuficiente para montar vídeo: {free:.0f} MB livres "
            f"(mínimo: {MIN_FREE_RAM_MB} MB). "
            f"Execute localmente ou aumente VIDEO_MIN_FREE_RAM_MB."
        )


# ─── Download de imagem ───────────────────────────────────────

async def download_image(url: str, dest: Path) -> bool:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            if r.status_code == 200:
                dest.write_bytes(r.content)
                return True
    except Exception as exc:
        log.warning(f"download_image falhou: {exc}")
    return False


# ─── Montagem principal ───────────────────────────────────────

async def assemble_video(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    title: str = "",
    cta: str = "Clique no link da bio!",
) -> tuple[bool, str | None]:
    """
    [OOM-2] Devolve (sucesso: bool, motivo_falha: str | None).
    Erros de OOM são capturados e devolvidos como mensagem legível.
    """
    # [OOM-1] Guarda de memória
    try:
        _check_ram()
    except VideoAssemblerError as exc:
        log.error(f"[OOM-1] {exc}")
        return False, str(exc)

    try:
        import ffmpeg
    except ImportError:
        msg = "ffmpeg-python não instalado. Execute: pip install ffmpeg-python"
        log.error(msg)
        return False, msg

    try:
        probe    = ffmpeg.probe(str(audio_path))
        duration = float(probe["streams"][0]["duration"])

        image_input = ffmpeg.input(str(image_path), loop=1, t=duration)
        audio_input = ffmpeg.input(str(audio_path))

        video = (
            image_input
            .filter("scale", 1080, 1920, force_original_aspect_ratio="increase")
            .filter("crop", 1080, 1920)
            .filter(
                "drawtext",
                text=cta,
                fontsize=52, fontcolor="white",
                x="(w-text_w)/2", y="h-120",
                shadowx=2, shadowy=2, shadowcolor="black",
                box=1, boxcolor="black@0.5", boxborderw=12,
            )
        )

        out = ffmpeg.output(
            video, audio_input,
            str(output_path),
            vcodec="libx264", acodec="aac",
            pix_fmt="yuv420p", r=30,
            shortest=None,
        )

        # [OOM-2] ffmpeg roda em subprocesso; código 137 = OOM kill pelo kernel
        ffmpeg.run(out, overwrite_output=True, quiet=True)
        return True, None

    except MemoryError:
        msg = "MemoryError durante montagem do vídeo — RAM esgotada."
        log.error(f"[OOM-2] {msg}")
        return False, msg

    except ffmpeg.Error as exc:
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        # Código 137 = SIGKILL por OOM
        if "137" in stderr or "Killed" in stderr:
            msg = "Processo ffmpeg morto pelo SO (OOM / código 137)."
            log.error(f"[OOM-2] {msg}")
            return False, msg
        msg = f"ffmpeg.Error: {stderr[:200]}"
        log.error(f"assemble_video falhou: {msg}")
        return False, msg

    except Exception as exc:
        msg = f"Erro inesperado na montagem: {exc}"
        log.error(msg)
        return False, msg


# ─── Orquestrador público ─────────────────────────────────────

async def build_deal_video(deal, work_dir: Path = WORK_DIR) -> Path | None:
    """
    Baixa imagem → monta vídeo.
    Devolve Path do vídeo final ou None em falha.
    Propaga VideoAssemblerError para que o caller possa alertar o admin.
    """
    slug        = deal.item_id
    img_path    = work_dir / f"{slug}_image.jpg"
    audio_path  = work_dir / f"{slug}_audio.wav"
    video_path  = work_dir / f"{slug}_final.mp4"

    if not audio_path.exists():
        log.warning(f"Áudio ausente para {slug} — gere TTS primeiro.")
        return None

    # [OOM-1] Verifica RAM antes de qualquer operação pesada
    _check_ram()   # lança VideoAssemblerError se RAM insuficiente

    if deal.image_url:
        ok = await download_image(deal.image_url, img_path)
        if not ok:
            img_path = None

    if img_path and img_path.exists():
        success, reason = await assemble_video(img_path, audio_path, video_path, title=deal.title)
        if success:
            return video_path
        # [OOM-3] Relança como VideoAssemblerError para o garimpeiro reagir
        raise VideoAssemblerError(reason or "Falha desconhecida na montagem.")

    return None
