"""
=============================================================
 Achadinhos do Momento — Organizador de Cross-posting
 Stack : Python puro (stdlib) + yt-dlp + Pillow
 Função: Pipeline offline que prepara o conteúdo para você
         fazer o upload manual no celular de forma eficiente.
=============================================================
O QUE ESTE SCRIPT FAZ:
  1. Lê uma "fila" de vídeos (pasta /queue ou arquivo CSV)
  2. Para cada vídeo, gera um "pacote de postagem":
     - Thumbnail otimizada (1:1 para Instagram, 9:16 para Reels/TikTok)
     - Caption completa com hashtags e CTAs por plataforma
     - Arquivo .txt com checklist de postagem manual
  3. Organiza tudo numa pasta /ready com subpastas por plataforma

POR QUE UPLOAD MANUAL?
  Redes como TikTok, Reels e Shorts penalizam posts via API de
  terceiros (shadowban). O upload manual pelo app garante acesso
  aos áudios virais nativos — que multiplicam o alcance orgânico.
  Este script elimina o trabalho *intelectual* da postagem;
  o único trabalho manual é apertar "publicar" no celular.
=============================================================
"""

import csv
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ─── Dependências opcionais (avisa se não instaladas) ────────
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  Pillow não instalado. Thumbnails não serão geradas.")
    print("   Instale com: pip install Pillow")

# ─── Config ──────────────────────────────────────────────────
QUEUE_DIR    = Path(os.getenv("QUEUE_DIR",    "queue"))    # Pasta com vídeos brutos
OUTPUT_DIR   = Path(os.getenv("OUTPUT_DIR",   "ready"))    # Saída organizada
AFFILIATE_URL= os.getenv("AFFILIATE_URL", "https://shopee.com.br/seu_link")
HANDLE       = os.getenv("HANDLE", "@achadinhosdomomento")

# ── Hashtags por plataforma ───────────────────────────────────
HASHTAGS = {
    "instagram": [
        "#achadinhos", "#shopee", "#achado", "#promoção",
        "#economize", "#ofertadodia", "#mercadolivre",
        "#dica", "#comprasinteligentes", "#produtosbaratosshopee",
    ],
    "tiktok": [
        "#achadinhos", "#shopee", "#achado", "#fyp",
        "#fy", "#paravocê", "#promoção", "#economize",
        "#compras", "#dica",
    ],
    "youtube": [
        "#Shorts", "#achadinhos", "#shopee", "#promoção",
        "#economize", "#achado",
    ],
}

# ── Templates de caption ─────────────────────────────────────
CAPTION_TEMPLATES = {
    "instagram": (
        "{title} ✨\n\n"
        "👇 Link na bio para comprar!\n\n"
        "💬 Comenta EU QUERO que eu mando o link direto no Direct!\n\n"
        "{hashtags}"
    ),
    "tiktok": (
        "{title} 🔥\n\n"
        "Link na bio! 👆\n\n"
        "{hashtags}"
    ),
    "youtube": (
        "{title}\n\n"
        "🔗 Link para comprar: {affiliate_url}\n\n"
        "Inscreva-se para mais achados todo dia!\n\n"
        "{hashtags}"
    ),
}


# ══════════════════════════════════════════════════════════════
# MODELO DE ITEM DA FILA
# ══════════════════════════════════════════════════════════════

@dataclass
class QueueItem:
    video_path    : str
    title         : str
    product_name  : str
    price         : Optional[str]   = None    # Ex: "R$ 49,90"
    old_price     : Optional[str]   = None    # Ex: "R$ 99,90"
    affiliate_url : str             = ""
    platforms     : list            = field(default_factory=lambda: ["instagram", "tiktok", "youtube"])
    notes         : str             = ""


# ══════════════════════════════════════════════════════════════
# LEITOR DA FILA
# ══════════════════════════════════════════════════════════════

def load_queue_from_csv(csv_path: Path) -> list[QueueItem]:
    """
    Lê a fila de postagens de um arquivo CSV.
    
    Formato esperado (crie no Excel/Sheets e exporte como CSV):
    video_path,title,product_name,price,old_price,affiliate_url,platforms,notes
    queue/video1.mp4,Tênis Nike baratíssimo,Tênis Nike Air,R$ 149,R$ 299,https://...,instagram;tiktok,postar às 19h
    """
    items = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            platforms = row.get("platforms", "instagram;tiktok;youtube").split(";")
            items.append(QueueItem(
                video_path    = row["video_path"],
                title         = row["title"],
                product_name  = row.get("product_name", row["title"]),
                price         = row.get("price"),
                old_price     = row.get("old_price"),
                affiliate_url = row.get("affiliate_url", AFFILIATE_URL),
                platforms     = [p.strip() for p in platforms],
                notes         = row.get("notes", ""),
            ))
    return items


def load_queue_from_folder(folder: Path) -> list[QueueItem]:
    """
    Cria uma fila automática a partir de vídeos em /queue.
    Usa o nome do arquivo como título.
    """
    items = []
    for video in sorted(folder.glob("*.mp4")):
        # Converte "tenis_nike_barato.mp4" → "Tenis Nike Barato"
        title = video.stem.replace("_", " ").replace("-", " ").title()
        items.append(QueueItem(
            video_path    = str(video),
            title         = title,
            product_name  = title,
            affiliate_url = AFFILIATE_URL,
        ))
    return items


# ══════════════════════════════════════════════════════════════
# GERADOR DE THUMBNAIL
# ══════════════════════════════════════════════════════════════

def generate_thumbnail(video_path: Path, output_dir: Path, aspect: str = "9:16") -> Optional[Path]:
    """
    Extrai frame do vídeo e gera thumbnail usando ffmpeg (se disponível).
    Retorna o caminho da thumbnail ou None se falhar.
    """
    try:
        import subprocess
        thumb_path = output_dir / "thumbnail.jpg"
        # Extrai o frame do segundo 1 do vídeo
        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-ss", "00:00:01",
            "-vframes", "1",
            "-q:v", "2",
            str(thumb_path)
        ], capture_output=True, text=True, timeout=30)

        if result.returncode == 0 and thumb_path.exists() and PIL_AVAILABLE:
            # Redimensiona para proporção correta
            img = Image.open(thumb_path)
            w, h = img.size
            
            if aspect == "1:1":
                # Crop quadrado centralizado
                size = min(w, h)
                left = (w - size) // 2
                top  = (h - size) // 2
                img  = img.crop((left, top, left+size, top+size))
                img  = img.resize((1080, 1080), Image.LANCZOS)
            else:  # 9:16
                img = img.resize((1080, 1920), Image.LANCZOS)

            img.save(thumb_path, "JPEG", quality=90)
            return thumb_path

    except (FileNotFoundError, Exception) as e:
        print(f"   ⚠️  Não foi possível gerar thumbnail: {e}")
    return None


# ══════════════════════════════════════════════════════════════
# GERADOR DE CAPTION
# ══════════════════════════════════════════════════════════════

def generate_caption(item: QueueItem, platform: str) -> str:
    """Gera a caption formatada para cada plataforma."""
    hashtags_str = " ".join(HASHTAGS.get(platform, []))
    template     = CAPTION_TEMPLATES.get(platform, "{title}\n{hashtags}")

    # Monta título com preço se disponível
    title_parts = [item.title]
    if item.price and item.old_price:
        title_parts.append(f"de ~~{item.old_price}~~ por {item.price}")
    elif item.price:
        title_parts.append(f"por apenas {item.price}")

    return template.format(
        title         = " ".join(title_parts),
        hashtags      = hashtags_str,
        affiliate_url = item.affiliate_url,
        handle        = HANDLE,
        product       = item.product_name,
    )


# ══════════════════════════════════════════════════════════════
# GERADOR DO CHECKLIST MANUAL
# ══════════════════════════════════════════════════════════════

PLATFORM_TIPS = {
    "instagram": [
        "Abra o Instagram pelo celular (NÃO pelo computador)",
        "Vá em '+' → Reel",
        "Escolha o vídeo da pasta /ready/instagram/",
        "⚠️  ADICIONE um áudio viral ANTES de postar (não use o áudio original)",
        "Cole a caption do arquivo caption_instagram.txt",
        "Marque produtos relevantes se tiver conta de afiliado verificada",
        "Poste no horário de pico: 19h–21h (BRT)",
    ],
    "tiktok": [
        "Abra o TikTok pelo celular",
        "Grave ou importe o vídeo da pasta /ready/tiktok/",
        "⚠️  ADICIONE um som em alta do próprio TikTok",
        "Cole a caption do arquivo caption_tiktok.txt",
        "Adicione o link do produto em 'Adicionar link'",
        "Poste no horário de pico: 18h–20h (BRT)",
    ],
    "youtube": [
        "Acesse youtube.com/upload pelo celular ou desktop",
        "Selecione o vídeo de /ready/youtube/",
        "Cole a caption (inclui link de afiliado na descrição)",
        "Defina como 'Não listado' para testar antes de tornar público",
        "Adicione à playlist 'Achadinhos do Momento'",
        "Shorts: vídeos verticais < 60s são promovidos automaticamente",
    ],
}

def generate_checklist(item: QueueItem, platform: str, caption: str) -> str:
    """Gera arquivo de checklist para postagem manual."""
    tips = PLATFORM_TIPS.get(platform, [])
    lines = [
        f"{'═'*60}",
        f"  CHECKLIST DE POSTAGEM — {platform.upper()}",
        f"{'═'*60}",
        f"📦 Produto  : {item.product_name}",
        f"💰 Preço    : {item.price or 'n/d'}",
        f"🔗 Link Af. : {item.affiliate_url}",
        f"📅 Data     : {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"📝 Notas    : {item.notes or 'nenhuma'}",
        f"\n{'─'*60}",
        f"  PASSOS PARA POSTAR",
        f"{'─'*60}",
    ]
    for i, tip in enumerate(tips, 1):
        lines.append(f"  [ ] {i}. {tip}")

    lines += [
        f"\n{'─'*60}",
        f"  CAPTION PARA COPIAR",
        f"{'─'*60}",
        caption,
        f"\n{'═'*60}",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════

def process_queue(items: list[QueueItem]):
    """Processa toda a fila e gera os pacotes de postagem."""
    if not items:
        print("⚠️  Fila vazia. Adicione vídeos em /queue ou crie queue.csv")
        return

    print(f"\n🎬 Processando {len(items)} itens da fila...\n")

    for idx, item in enumerate(items, 1):
        video_path = Path(item.video_path)
        print(f"[{idx}/{len(items)}] {item.title}")

        if not video_path.exists():
            print(f"   ⚠️  Arquivo não encontrado: {video_path}. Pulando...")
            continue

        for platform in item.platforms:
            # Cria pasta de saída por plataforma
            platform_dir = OUTPUT_DIR / f"{idx:02d}_{video_path.stem}" / platform
            platform_dir.mkdir(parents=True, exist_ok=True)

            # 1. Copia o vídeo
            shutil.copy2(video_path, platform_dir / video_path.name)

            # 2. Gera thumbnail
            thumb = generate_thumbnail(video_path, platform_dir,
                                       aspect="1:1" if platform == "instagram" else "9:16")
            if thumb:
                print(f"   🖼️  Thumbnail gerada para {platform}")

            # 3. Gera caption
            caption = generate_caption(item, platform)
            (platform_dir / f"caption_{platform}.txt").write_text(caption, encoding="utf-8")

            # 4. Gera checklist
            checklist = generate_checklist(item, platform, caption)
            (platform_dir / f"CHECKLIST_{platform.upper()}.txt").write_text(checklist, encoding="utf-8")

            print(f"   ✅ {platform}: pacote gerado em {platform_dir}")

        # Salva metadados JSON para referência
        meta = asdict(item)
        meta["processed_at"] = datetime.now().isoformat()
        (OUTPUT_DIR / f"{idx:02d}_{video_path.stem}" / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2)
        )

    print(f"\n🎉 Pronto! Pacotes gerados em: {OUTPUT_DIR.absolute()}")
    print(f"   Transfira a pasta /ready para o celular e siga os checklists.\n")


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Garante que as pastas existam
    QUEUE_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Tenta carregar de CSV primeiro, depois da pasta
    csv_path = Path("queue.csv")
    if csv_path.exists():
        print(f"📋 Carregando fila de {csv_path}...")
        items = load_queue_from_csv(csv_path)
    else:
        print(f"📁 Carregando vídeos de {QUEUE_DIR}/...")
        items = load_queue_from_folder(QUEUE_DIR)

    process_queue(items)
