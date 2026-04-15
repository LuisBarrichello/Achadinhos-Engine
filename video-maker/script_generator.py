import httpx

PROMPT_TEMPLATE = """
Crie um roteiro de vídeo curto para afiliados (30-45 segundos).
Produto: {title}
Preço: R$ {price} (era R$ {original_price}) — {discount}% OFF
Link: {url}

Formato obrigatório:
HOOK: (1 frase impactante, máx 8 palavras)
PRODUTO: (o que é, 1 frase)
BENEFÍCIO: (por que comprar agora, 1 frase)
PROVA: (desconto/avaliação, 1 frase)
CTA: (chamada para ação, 1 frase curta)

Escreva em português brasileiro, tom urgente mas natural. Sem emojis.
"""


async def generate_script(deal) -> dict:
    prompt = PROMPT_TEMPLATE.format(
        title=deal.title,
        price=deal.price or "?",
        original_price=deal.original_price or "?",
        discount=deal.discount_pct or "?",
        url=deal.affiliate_url,
    )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",  # mais barato, suficiente
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}]
            }
        )
    text = resp.json()["content"][0]["text"]

    # Parseia as seções
    sections = {}
    for line in text.strip().split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            sections[key.strip()] = val.strip()
    return sections


def script_to_narration(sections: dict) -> str:
    """Junta as seções em texto corrido para o TTS."""
    return " ".join([
        sections.get("HOOK", ""),
        sections.get("PRODUTO", ""),
        sections.get("BENEFÍCIO", ""),
        sections.get("PROVA", ""),
        sections.get("CTA", ""),
    ])