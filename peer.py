"""Два аккаунта в одном репозитории: каждый читает журнал публикаций другого (только чтение), чтобы не повторять
одни и те же монеты почти одновременно и не ставить посты в одну минуту. Binance может счесть такие посты дублями и
урезать охват обоим аккаунтам."""
import json
import re
import sqlite3
import time

import config

_CASHTAG = re.compile(r"\$[A-Za-z0-9]{2,10}")
ALWAYS_OK = {"$BTC", "$ETH"}          # общие рыночные тикеры в обзорах не блокируем


def _peer_log() -> list:
    """square_post_log соседнего аккаунта из его файла состояния (на момент последнего коммита). [] если файла нет."""
    path = config.PEER_DB_PATH
    if not path.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT value FROM state WHERE key = 'square_post_log'").fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row else []
    except (sqlite3.Error, ValueError):
        return []


def recent_heads(hours: float = 12.0) -> str:
    """Начала текстов постов соседа за последние `hours` часов одной строкой (для проверки "эта монета уже была")."""
    now = time.time()
    return " ".join(p.get("head", "") for p in _peer_log() if now - p.get("ts", 0) < hours * 3600)


def seconds_since_last_post() -> float:
    log = _peer_log()
    return time.time() - max((p.get("ts", 0) for p in log), default=0)


def cashtags(text: str) -> set:
    return {t.upper() for t in _CASHTAG.findall(text or "")} - ALWAYS_OK


def clashes(text: str, hours: float = 12.0) -> bool:
    """Есть ли в тексте монета, о которой сосед писал за последние `hours` часов."""
    mine = cashtags(text)
    return bool(mine) and bool(mine & cashtags(recent_heads(hours)))


def recent_post_heads(hours: float = 36.0, fmts=None) -> list:
    """Начала постов соседа списком (для проверки "эта новость уже была"). Только у профиля b: основной аккаунт приоритетный
    и от второго не зависит."""
    if config.PROFILE != "b":
        return []
    now = time.time()
    return [p.get("head", "") for p in _peer_log() if now - p.get("ts", 0) < hours * 3600 and (fmts is None or p.get("fmt") in fmts)]
