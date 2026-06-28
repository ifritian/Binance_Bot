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
from signal_parser import RsiSignal

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


# --- Случайный разброс окна публикации ---
# Решается ОДИН РАЗ после каждой публикации (не на каждом тике, иначе
# порог "плавал" бы туда-сюда и было бы непредсказуемо). Хранится до
# следующей публикации этого формата, потом пересчитывается заново.

def get_jitter_seconds(post_type: str) -> float:
    return _get(f"jitter_seconds:{post_type}", 0)


def roll_new_jitter(post_type: str, max_jitter_seconds: float) -> float:
    """Бросает новый случайный разброс в диапазоне [-max, +max] и
    сохраняет его для следующего окна публикации этого формата."""
    import random

    value = random.uniform(-max_jitter_seconds, max_jitter_seconds)
    _set(f"jitter_seconds:{post_type}", value)
    return value


# --- История дайджестов за последние дни - для еженедельной статьи ---
# Храним отдельно от "отложенного поста" (pending_post) - это лог ВСЕХ
# увиденных дайджестов, а не только последнего, чтобы статья могла
# подвести итог за неделю.

_HISTORY_MAX_AGE_SECONDS = 9 * 24 * 3600  # держим чуть больше недели "на всякий"
_HISTORY_MAX_ENTRIES = 200  # защитный потолок - не даём промпту статьи разрастись, что бы ни писало в историю


def log_signal_history(signal: RsiSignal) -> None:
    history = _get("digest_history", [])
    history.append({
        "ticker": signal.ticker,
        "timeframe": signal.timeframe,
        "direction": signal.direction,
        "strategy": signal.strategy,
        "change_pct": signal.change_24h,
        "score": signal.score,
        "ts": time.time(),
    })
    cutoff = time.time() - _HISTORY_MAX_AGE_SECONDS
    history = [h for h in history if h["ts"] >= cutoff]
    if len(history) > _HISTORY_MAX_ENTRIES:
        history = history[-_HISTORY_MAX_ENTRIES:]
    _set("digest_history", history)


def get_digest_history(since_seconds_ago: float) -> list[dict]:
    """Возвращает записи истории не старше since_seconds_ago секунд назад,
    не больше _HISTORY_MAX_ENTRIES штук (на случай, если в базе уже
    накопилось больше из-за прошлых версий кода)."""
    history = _get("digest_history", [])
    cutoff = time.time() - since_seconds_ago
    recent = [h for h in history if h["ts"] >= cutoff]
    return recent[-_HISTORY_MAX_ENTRIES:]


# --- Недавно опубликованные тикеры - для разнообразия (избегаем повторов) ---

_RECENT_TICKERS_LIMIT = 3


def get_recent_tickers() -> list[str]:
    return _get("recent_tickers", [])


def log_posted_ticker(ticker: str) -> None:
    history = get_recent_tickers()
    history.append(ticker.upper())
    history = history[-_RECENT_TICKERS_LIMIT:]
    _set("recent_tickers", history)


# --- Кэш сопоставления тикер -> CoinGecko id ---
# Чтобы не дёргать /search на CoinGecko повторно для уже встречавшихся
# тикеров - результат поиска сохраняется один раз и переживает перезапуски.

def get_cached_coingecko_id(ticker: str) -> Optional[str]:
    return _get(f"coingecko_id:{ticker.upper()}", None)


def set_cached_coingecko_id(ticker: str, coingecko_id: str) -> None:
    _set(f"coingecko_id:{ticker.upper()}", coingecko_id)


# --- Отложенный пост, ждущий своего окна публикации ---
# Может быть двух видов: "digest" (текстовый дайджест с числами)
# или "image" (качественный инсайт по картинке, без чисел).

# --- Последняя использованная тема поста-мнения - для ротации ---

def get_last_opinion_theme() -> Optional[str]:
    return _get("last_opinion_theme", None)


def set_last_opinion_theme(theme: str) -> None:
    _set("last_opinion_theme", theme)


# --- Последний использованный режим тона хука - для ротации ---

def get_last_hook_mode() -> Optional[str]:
    return _get("last_hook_mode", None)


def set_last_hook_mode(mode: str) -> None:
    _set("last_hook_mode", mode)


# --- Очередь отложенных постов, ждущих своего окна публикации ---
# ВАЖНО: это настоящая FIFO-очередь, а не одно перезаписываемое
# значение. Раньше "отложенный пост" был ОДНИМ слотом - если за тик
# в канале набегало несколько сигналов, каждый следующий просто
# перетирал предыдущий, и публиковался только последний из пачки,
# а остальные терялись безо всякого лога. Теперь каждый новый сигнал
# или картинка добавляется в конец списка и ждёт своей очереди.
#
# У каждой записи есть счётчик попыток публикации (attempts) - если
# конкретный пост не публикуется несколько раз подряд (например,
# для тикера так и не нашёлся график), он сбрасывается из очереди,
# чтобы не блокировать навечно всё, что скопилось за ним.

_MAX_QUEUE_LENGTH = 30   # на случай аномального наплыва сигналов
MAX_PUBLISH_ATTEMPTS = 3


def _get_queue() -> list[dict]:
    return _get("post_queue", [])


def _set_queue(queue: list[dict]) -> None:
    _set("post_queue", queue)


def _push_pending(kind: str, payload: dict) -> None:
    queue = _get_queue()
    queue.append({"kind": kind, "payload": payload, "attempts": 0})
    if len(queue) > _MAX_QUEUE_LENGTH:
        dropped = queue.pop(0)
        import logging
        logging.getLogger("queue_manager").warning(
            "Очередь переполнена (>%d) - старейшая запись (%s) выброшена без публикации",
            _MAX_QUEUE_LENGTH, dropped.get("kind"),
        )
    _set_queue(queue)


def push_pending_signal(signal: RsiSignal) -> None:
    _push_pending("signal", asdict(signal))


def push_pending_image(insight: ImageInsight) -> None:
    _push_pending("image", asdict(insight))


def pending_queue_length() -> int:
    return len(_get_queue())


def pending_queue_summary() -> list[str]:
    """Короткое описание очереди для диагностики (check_state.py)."""
    out = []
    for item in _get_queue():
        ticker = item["payload"].get("ticker", "?")
        out.append(f"{item['kind']}:{ticker} (попыток={item['attempts']})")
    return out


def get_pending_post(min_score: int = 0) -> Optional[tuple[int, str, object]]:
    """Возвращает (индекс_в_очереди, kind, payload) ЛУЧШЕГО подходящего
    поста, или None, если ничего не подходит.

    "Лучший" = сигнал (kind=signal) с максимальным score СРЕДИ ТЕХ, у
    кого score > min_score. Если ни один сигнал не проходит порог -
    рассматривается самый старый пост типа "image" (для картинок score
    не считается, порог на них не действует - это отдельный, более
    редкий путь публикации).

    Если ничего не подходит вообще - очередь НЕ трогаем, просто ждём
    следующего тика (новый сигнал может появиться, либо существующий
    станет неактуальным и выпадет по лимиту попыток/переполнению)."""
    queue = _get_queue()
    if not queue:
        return None

    best_idx, best_score = None, None
    fallback_image_idx = None

    for idx, item in enumerate(queue):
        if item["kind"] == "signal":
            try:
                score = int(item["payload"].get("score", 0))
            except (TypeError, ValueError):
                score = 0
            if score > min_score and (best_score is None or score > best_score):
                best_idx, best_score = idx, score
        elif item["kind"] == "image" and fallback_image_idx is None:
            fallback_image_idx = idx

    chosen_idx = best_idx if best_idx is not None else fallback_image_idx
    if chosen_idx is None:
        return None

    item = queue[chosen_idx]
    kind = item["kind"]
    payload = item["payload"]

    if kind == "signal":
        return chosen_idx, kind, RsiSignal(**payload)
    return chosen_idx, kind, ImageInsight(**payload)


def clear_pending_post(index: int) -> None:
    """Убирает конкретный пост из очереди (по индексу) - вызывать после успешной публикации."""
    queue = _get_queue()
    if 0 <= index < len(queue):
        queue.pop(index)
        _set_queue(queue)


def register_failed_attempt(index: int) -> bool:
    """Увеличивает счётчик попыток у конкретного поста в очереди (по
    индексу). Если попыток стало больше лимита - выбрасывает его из
    очереди и возвращает True. Иначе возвращает False (попробуем снова
    на следующем тике)."""
    queue = _get_queue()
    if not (0 <= index < len(queue)):
        return False

    queue[index]["attempts"] += 1
    dropped = queue[index]["attempts"] > MAX_PUBLISH_ATTEMPTS
    if dropped:
        queue.pop(index)
    _set_queue(queue)
    return dropped


# --- Cooldown для собственного сканера сигналов (scanner.py) ---
# Без этого, пока RSI пары держится за пределами 70/30 (а это может
# длиться часами), сканер заносил бы в очередь практически идентичный
# сигнал на каждом тике (раз в 10 минут).

def was_recently_alerted(ticker: str, direction_key: str, cooldown_hours: float) -> bool:
    key = f"scanner_alert:{ticker.upper()}:{direction_key}"
    last_ts = _get(key, None)
    if last_ts is None:
        return False
    return (time.time() - last_ts) < cooldown_hours * 3600


def mark_alerted(ticker: str, direction_key: str) -> None:
    key = f"scanner_alert:{ticker.upper()}:{direction_key}"
    _set(key, time.time())