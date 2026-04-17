"""
shared/matching.py — Lógica centralizada de keyword matching.

[FIX-6.1] Elimina a duplicação entre:
  - backend/api/routes/webhooks.py
  - frontend/api/webhook.py

Importe daqui em ambos os contextos.
"""

import re
import unicodedata


def normalize_keyword(text: str) -> str:
    """Remove acentos, pontuação e normaliza para uppercase."""
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", text)
    ascii_text = nfd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]", "", ascii_text.upper())


def keyword_matches(comment: str, keyword: str) -> bool:
    """
    Retorna True se `keyword` (normalizada) está contida em `comment` (normalizado).
    Case-insensitive, sem acentos, sem pontuação.
    """
    norm_comment = normalize_keyword(comment)
    norm_keyword = normalize_keyword(keyword)
    if not norm_keyword:
        return False
    return norm_keyword in norm_comment


# Aliases internos mantidos para retrocompatibilidade nos módulos antigos
_normalize_keyword = normalize_keyword
_keyword_matches = keyword_matches
