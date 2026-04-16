from datetime import date

# Estado in-memory — fonte verdade é a tabela webhook_events,
# este contador serve apenas para exibição rápida no /status.
_dm_counters: dict[str, int] = {}


def _increment_dm_today() -> None:
    today = str(date.today())
    _dm_counters[today] = _dm_counters.get(today, 0) + 1


def _dm_count_today() -> int:
    return _dm_counters.get(str(date.today()), 0)
