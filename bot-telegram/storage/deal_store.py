"""
bot-telegram/storage/deal_store.py — Persistência de deals processados.

[FIX-6.3] Implementa TTL configurável:
  - Chaves mais antigas que DEAL_TTL_DAYS são removidas automaticamente
  - purge() é chamado em mark() e pode ser chamado explicitamente
  - Produtos antigos reaparecem no feed após expirar o TTL
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("garimpeiro")

# Valor padrão (pode ser sobrescrito via Config)
DEFAULT_TTL_DAYS = 14


class DealStore:
    def __init__(self, path: Path, ttl_days: int = DEFAULT_TTL_DAYS) -> None:
        self._path    = path
        self._ttl_sec = ttl_days * 86_400
        self._store   : dict[str, int] = self._load()
        # Purga imediatamente ao iniciar para limpar o arquivo existente
        purged = self._purge()
        if purged:
            log.info(f"DealStore: {purged} deal(s) expirado(s) removido(s) na inicialização")

    # ── Carga ─────────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, int]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open(encoding="utf-8") as f:
                raw = json.load(f)
            # Retrocompatibilidade: arquivo antigo pode ser lista ou dict sem ts
            if isinstance(raw, list):
                log.info("DealStore: migrando formato legado (lista → dict com timestamp)")
                return {k: 0 for k in raw}
            if isinstance(raw, dict):
                return {k: int(v) for k, v in raw.items()}
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"DealStore: erro ao ler {self._path}: {e}")
        return {}

    # ── TTL ───────────────────────────────────────────────────────────────────

    def _purge(self) -> int:
        """Remove entradas mais antigas que o TTL. Retorna quantidade removida."""
        if self._ttl_sec <= 0:
            return 0
        cutoff = int(time.time()) - self._ttl_sec
        expired = [k for k, ts in self._store.items() if ts < cutoff]
        for k in expired:
            del self._store[k]
        return len(expired)

    # ── API pública ───────────────────────────────────────────────────────────

    def already_seen(self, key: str) -> bool:
        """Verifica se o deal já foi processado e ainda está dentro do TTL."""
        ts = self._store.get(key)
        if ts is None:
            return False
        if self._ttl_sec > 0 and (int(time.time()) - ts) > self._ttl_sec:
            # Expirado inline — remove e permite reprocessar
            del self._store[key]
            log.debug(f"DealStore: '{key}' expirado, será reprocessado")
            return False
        return True

    def mark(self, key: str) -> None:
        self._store[key] = int(time.time())
        # Aproveita cada mark() para purgar até 10 expirados (lazy cleanup)
        if len(self._store) % 50 == 0:
            purged = self._purge()
            if purged:
                log.debug(f"DealStore: lazy purge removeu {purged} deal(s) expirado(s)")

    def flush(self) -> None:
        """Persiste o store em disco de forma atômica."""
        # Purga antes de salvar para manter o arquivo enxuto
        self._purge()
        tmp = self._path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._store, f, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
            log.debug(f"DealStore: {len(self._store)} deal(s) persistido(s)")
        except IOError as e:
            log.error(f"DealStore: falha ao persistir: {e}")
            tmp.unlink(missing_ok=True)

    def stats(self) -> dict:
        """Retorna estatísticas do store para log/debug."""
        now = int(time.time())
        if self._ttl_sec > 0:
            active = sum(1 for ts in self._store.values() if (now - ts) <= self._ttl_sec)
        else:
            active = len(self._store)
        return {"total": len(self._store), "active": active, "ttl_days": self._ttl_sec // 86_400}
