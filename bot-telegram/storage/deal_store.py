import json
import logging
import time
from pathlib import Path

log = logging.getLogger("garimpeiro")


class DealStore:
    def __init__(self, path: Path) -> None:
        self._path  = path
        self._store : dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open(encoding="utf-8") as f:
                raw = json.load(f)
            return raw if isinstance(raw, dict) else {k: 0 for k in raw}
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"DealStore: erro ao ler {self._path}: {e}")
        return {}

    def already_seen(self, key: str) -> bool:
        return key in self._store

    def mark(self, key: str) -> None:
        self._store[key] = int(time.time())

    def flush(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._store, f, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
        except IOError as e:
            log.error(f"DealStore: falha ao persistir: {e}")
            tmp.unlink(missing_ok=True)
