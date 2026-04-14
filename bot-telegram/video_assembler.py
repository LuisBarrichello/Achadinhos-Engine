import asyncio
import httpx
from pathlib import Path
from datetime import datetime

WORK_DIR = Path("./temp_videos")
WORK_DIR.mkdir(exist_ok=True)


async def download_image(url: str, dest: Path) -> bool:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            return True
    return False


async def assemble_video(
        image_path: Path,
        audio_path: Path,
        output_path: Path,
        title: str = "",
        cta: str = "Clique no link da bio!",
) -> bool:
    """
    Monta vídeo 9:16 (1080x1920) com:
    - Imagem do produto como fundo (com blur nas bordas)
    - Áudio do TTS
    - Texto do CTA na parte inferior

    Requer: pip install ffmpeg-python
    e ffmpeg instalado no sistema
    """
    import ffmpeg  # pip install ffmpeg-python

    try:
        # Duração do áudio
        probe = ffmpeg.probe(str(audio_path))
        duration = float(probe['streams'][0]['duration'])

        # Pipeline ffmpeg
        image_input = ffmpeg.input(str(image_path), loop=1, t=duration)
        audio_input = ffmpeg.input(str(audio_path))

        video = (
            image_input
            .filter('scale', 1080, 1920, force_original_aspect_ratio='increase')
            .filter('crop', 1080, 1920)
            .filter('drawtext',
                    text=cta,
                    fontsize=52, fontcolor='white',
                    x='(w-text_w)/2', y='h-120',
                    shadowx=2, shadowy=2, shadowcolor='black',
                    box=1, boxcolor='black@0.5', boxborderw=12
                    )
        )

        out = ffmpeg.output(
            video, audio_input,
            str(output_path),
            vcodec='libx264', acodec='aac',
            pix_fmt='yuv420p', r=30,
            shortest=None,
        )
        ffmpeg.run(out, overwrite_output=True, quiet=True)
        return True

    except Exception as e:
        print(f"Erro na montagem: {e}")
        return False


async def build_deal_video(deal, work_dir: Path = WORK_DIR) -> Path | None:
    """
    Orquestra: baixa imagem → monta vídeo com áudio existente.
    Retorna o caminho do vídeo final ou None em caso de falha.
    """
    slug = deal.item_id
    img_path = work_dir / f"{slug}_image.jpg"
    audio_path = work_dir / f"{slug}_audio.wav"
    video_path = work_dir / f"{slug}_final.mp4"

    if not audio_path.exists():
        return None  # TTS deve rodar antes

    if deal.image_url:
        ok = await download_image(deal.image_url, img_path)
        if not ok:
            img_path = None

    if img_path and img_path.exists():
        ok = await assemble_video(img_path, audio_path, video_path,
                                  title=deal.title)
        if ok:
            return video_path

    return None