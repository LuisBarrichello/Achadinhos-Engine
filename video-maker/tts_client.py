"""
tts_client.py — Wrapper assíncrono para Kokoro e Fish Speech locais.
Chama os scripts via subprocess (sem servidor HTTP).
"""

import asyncio
import os
import tempfile
from pathlib import Path

# ── Config via .env ───────────────────────────────────────────
TTS_ENGINE       = os.getenv("TTS_ENGINE", "kokoro")       # "kokoro" ou "fish"
KOKORO_SCRIPT    = os.getenv("KOKORO_SCRIPT", "/home/user/tts_batch.py")
KOKORO_VENV      = os.getenv("KOKORO_VENV",  "/home/user/tts-env/bin/python")
FISH_SCRIPT      = os.getenv("FISH_SCRIPT",  "/home/user/fish-speech/fish_batch.py")
FISH_VENV        = os.getenv("FISH_VENV",    "/home/user/fish-speech/venv/bin/python")
FISH_DIR         = os.getenv("FISH_DIR",     "/home/user/fish-speech")  # cwd obrigatório
TTS_TIMEOUT      = int(os.getenv("TTS_TIMEOUT", "120"))    # segundos


async def synthesize(text: str, output_path: Path) -> bool:
    """
    Gera áudio a partir de texto usando o engine configurado.
    Escreve o resultado em output_path (.wav).
    Retorna True em sucesso, False em falha.
    """
    engine = TTS_ENGINE.lower()

    if engine == "kokoro":
        return await _run_kokoro(text, output_path)
    elif engine == "fish":
        return await _run_fish(text, output_path)
    else:
        raise ValueError(f"TTS_ENGINE inválido: '{engine}'. Use 'kokoro' ou 'fish'.")


async def _run_kokoro(text: str, output_path: Path) -> bool:
    """
    Chama tts_batch.py do Kokoro.
    Usa arquivo temporário de entrada (o script espera um .txt).
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(text)
        tmp_input = tmp.name

    cmd = [KOKORO_VENV, KOKORO_SCRIPT, tmp_input, str(output_path)]

    return await _run_subprocess(cmd, cwd=None, label="Kokoro", cleanup=tmp_input)


async def _run_fish(text: str, output_path: Path) -> bool:
    """
    Chama fish_batch.py do Fish Speech.
    Precisa rodar com cwd=FISH_DIR (o script usa paths relativos).
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(text)
        tmp_input = tmp.name

    cmd = [FISH_VENV, FISH_SCRIPT, tmp_input, str(output_path)]

    return await _run_subprocess(cmd, cwd=FISH_DIR, label="Fish Speech", cleanup=tmp_input)


async def _run_subprocess(
    cmd: list[str],
    cwd: str | None,
    label: str,
    cleanup: str | None = None,
) -> bool:
    """Executa o subprocess de forma assíncrona com timeout."""
    import logging
    log = logging.getLogger("tts_client")

    log.info(f"🎙️  [{label}] Iniciando síntese...")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=TTS_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            log.error(f"[{label}] Timeout após {TTS_TIMEOUT}s")
            return False

        if proc.returncode != 0:
            log.error(f"[{label}] Falhou (returncode={proc.returncode})")
            log.error(f"[{label}] stderr: {stderr.decode()[:300]}")
            return False

        log.info(f"✅ [{label}] Síntese concluída")
        return True

    except FileNotFoundError:
        log.error(f"[{label}] Python/script não encontrado: {cmd[0]}")
        return False
    except Exception as e:
        log.error(f"[{label}] Erro inesperado: {e}")
        return False
    finally:
        # Limpa o arquivo temporário de input
        if cleanup:
            try:
                Path(cleanup).unlink(missing_ok=True)
            except Exception:
                pass