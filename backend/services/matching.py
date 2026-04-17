import re
import unicodedata


def _normalize_keyword(text: str) -> str:
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", text)
    ascii_text = nfd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]", "", ascii_text.upper())


def keyword_matches(comment: str, keyword: str) -> bool:
    norm_comment = _normalize_keyword(comment)
    norm_keyword  = _normalize_keyword(keyword)
    if not norm_keyword:
        return False
    return norm_keyword in norm_comment
