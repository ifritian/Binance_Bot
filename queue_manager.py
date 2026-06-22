"""
Состояние бота в SQLite: id последнего просмотренного поста канала,
время последней публикации, и "отложенный" дайджест, который ждёт
своего окна публикации (>4ч с прошлого поста).

SQLite выбран по той же причине, что и в проекте: ничего не нужно
поднимать отдельно, файл bot_state.db просто лежит рядом со скриптом
и переживает перезапуски.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from typing import Optional

import config
from image_analyzer import ImageInsight
from signal_parser import Signal, FollowUpEntry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    try:
        conn.execute(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _get(key: str, default=None):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default


def _set(key: str, value) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


# --- id последнего просмотренного поста в канале ---

def get_telegram_update_offset() -> int:
    return _get("telegram_update_offset", 0)


def set_telegram_update_offset(update_id: int) -> None:
    _set("telegram_update_offset", update_id)


# --- Время последней публикации, отдельно по каждому формату поста ---
# "currency" - пост про валюту (раз в 4ч), "opinion" - личное мнение
# (раз в 2 дня), "article" - статья (раз в неделю). Форматы независимы
# друг от друга - могут публиковаться в один день, если так совпало.

def get_last_post_time(post_type: str = "currency") -> float:
    return _get(f"last_post_time:{post_type}", 0)


def set_last_post_time(post_type: str = "currency", ts: Optional[float] = None) -> None:
    _set(f"last_post_time:{post_type}", ts if ts is not None else time.time())


def seconds_since_last_post(post_type: str = "currency") -> float:
    last = get_last_post_time(post_type)
    if last == 0:
        return float("inf")
    return time.time() - last


# --- История дайджестов за последние дни - для еженедельной статьи ---
# Храним отдельно от "отложенного поста" (pending_post) - это лог ВСЕХ
# увиденных дайджестов, а не только последнего, чтобы статья могла
# подвести итог за неделю.

_HISTORY_MAX_AGE_SECONDS = 9 * 24 * 3600  # держим чуть больше недели "на всякий"


def log_digest_history(entry: FollowUpEntry, digest_title: str) -> None:
    history = _get("digest_history", [])
    history.append({
        "ticker": entry.ticker,
        "timeframe": entry.timeframe,
        "result": entry.result,
        "change_pct": entry.change_pct,
        "score": entry.score,
        "digest_title": digest_title,
        "ts": time.time(),
    })
    cutoff = time.time() - _HISTORY_MAX_AGE_SECONDS
    history = [h for h in history if h["ts"] >= cutoff]
    _set("digest_history", history)


def get_digest_history(since_seconds_ago: float) -> list[dict]:
    """Возвращает записи истории не старше since_seconds_ago секунд назад."""
    history = _get("digest_history", [])
    cutoff = time.time() - since_seconds_ago
    return [h for h in history if h["ts"] >= cutoff]


# --- Отложенный пост, ждущий своего окна публикации ---
# Может быть двух видов: "digest" (текстовый дайджест с числами)
# или "image" (качественный инсайт по картинке, без чисел).

def get_pending_post() -> Optional[tuple[str, object]]:
    """Возвращает (kind, payload) или None, если очередь пуста."""
    data = _get("pending_post", None)
    if not data:
        return None

    kind = data["kind"]
    payload = data["payload"]

    if kind == "digest":
        entries = [FollowUpEntry(**e) for e in payload["entries"]]
        return kind, Signal(title=payload["title"], entries=entries, raw_text=payload["raw_text"])
    if kind == "image":
        return kind, ImageInsight(**payload)

    return None


def set_pending_digest(signal: Signal) -> None:
    _set("pending_post", {"kind": "digest", "payload": asdict(signal)})


def set_pending_image(insight: ImageInsight) -> None:
    _set("pending_post", {"kind": "image", "payload": asdict(insight)})


def clear_pending_post() -> None:
    _set("pending_post", None)