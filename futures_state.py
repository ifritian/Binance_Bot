"""
futures_state.py - собственное состояние futures-бота: kill switch и
дневной baseline (risk_guard.py), и собственный cooldown сканирования
рынка (см. was_recently_alerted/mark_alerted ниже) - в ОТДЕЛЬНОМ файле
config.FUTURES_DB_PATH, НЕ в bot_state.db постинг-бота.

ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ ПРОСТО ДРУГИЕ КЛЮЧИ В bot_state.db:
bot_state.db коммитит и пушит сам постинг-бот через GitHub Actions (см.
.github/workflows/bot.yml, шаг "Обновление состояния бота [skip ci]") -
КАЖДЫЙ прогон workflow коммитит и пушит его. Пока risk_guard.py писал
kill switch/baseline прямо в bot_state.db, любой ЛОКАЛЬНЫЙ запуск
futures-скриптов (risk_guard_cli.py, futures_auto_trade.py) создавал
локальные изменения этого файла, которые регулярно конфликтовали с тем,
что GitHub Actions успевал запушить между твоими запусками - см. реальный
случай: "Your local changes... would be overwritten by merge: bot_state.db".

Два бота (постинг - main.py/scanner.py/queue_manager.py, и futures -
futures_auto_trade.py/risk_guard.py/futures_state.py) теперь ПОЛНОСТЬЮ
независимы по части состояния - у каждого свой файл, свой cooldown
сканирования, futures-бот НИКОГДА не пишет (и не читает) bot_state.db.
"Обмен информацией" между ними - на уровне ОБЩЕГО КОДА (оба используют
один и тот же scanner.py для поиска сигналов - см. параметр state=
у scanner.run_scan, которым futures_auto_trade.py передаёт СЕБЯ вместо
queue_manager по умолчанию), а не общего файла состояния.

FUTURES_DB_PATH сознательно НЕ в git (см. .gitignore) - это чисто
локальное runtime-состояние (kill switch, baseline, cooldown), имеющее
смысл per-машина/per-окружение, а не история для коммитов. Если когда-
нибудь появится необходимость гонять futures-бота по расписанию через
GitHub Actions - это отдельное осознанное решение (см. docstring
futures_auto_trade.py), и тогда можно будет по аналогии с bot.yml
завести СВОЙ шаг коммита futures_state.db в СВОЙ workflow - не переиспользуя
bot_state.db постинг-бота.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(config.FUTURES_DB_PATH)
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


# --- предохранители риска (см. risk_guard.py) ---

def get_risk_daily_baseline(day_key: str) -> Optional[float]:
    """Баланс кошелька, зафиксированный при ПЕРВОЙ проверке за
    UTC-день day_key (например '2026-07-29') - см. risk_guard._daily_loss_pct."""
    return _get(f"risk_daily_baseline:{day_key}", None)


def set_risk_daily_baseline(day_key: str, balance: float) -> None:
    _set(f"risk_daily_baseline:{day_key}", balance)


def get_kill_switch() -> Optional[dict]:
    """{'reason': str, 'tripped_at': timestamp}, если сработал один из
    предохранителей risk_guard.py, иначе None. Не привязан к дню/сессии -
    остаётся взведённым, пока его явно не снимут (см. clear_kill_switch,
    вызывается risk_guard_cli.py reset)."""
    return _get("risk_kill_switch", None)


def set_kill_switch(reason: str) -> None:
    _set("risk_kill_switch", {"reason": reason, "tripped_at": time.time()})


def clear_kill_switch() -> None:
    _set("risk_kill_switch", None)


# --- собственный cooldown сканирования рынка (см. scanner.py, параметр
# state=futures_state в futures_auto_trade.py) ---
#
# Та же роль, что у queue_manager.was_recently_alerted/mark_alerted для
# постинг-бота, но СВОЙ, а не общий - futures-бот не должен молчать про
# сигнал только потому, что постинг-бот недавно про него написал (и
# наоборот). Совпадающая сигнатура функций - это и есть контракт, который
# scanner.py ожидает от параметра state (см. scanner._process_signal_candidate).

def was_recently_alerted(ticker: str, direction_key: str, cooldown_hours: float) -> bool:
    last = _get(f"scan_alerted:{ticker}:{direction_key}", None)
    if last is None:
        return False
    return (time.time() - last) < cooldown_hours * 3600


def mark_alerted(ticker: str, direction_key: str) -> None:
    _set(f"scan_alerted:{ticker}:{direction_key}", time.time())


def push_pending_signal(signal) -> None:
    """Намеренно ничего не делает: futures-боту не нужна очередь постов
    (та часть контракта scanner.py, что относится ТОЛЬКО к постинг-боту) -
    см. docstring модуля про то, почему состояния ботов разделены."""
    return None


# --- троттлинг алертов владельцу (см. alerting.send_owner_alert,
# параметр state) - своя копия, чтобы futures_position_monitor.py могла
# слать алерты о сделках, не трогая bot_state.db постинг-бота. ---

def get_last_alert_sent(alert_key: str) -> float:
    return _get(f"alert_sent:{alert_key}", 0)


def set_last_alert_sent(alert_key: str) -> None:
    _set(f"alert_sent:{alert_key}", time.time())


# --- реестр позиций, которые futures-бот отслеживает для трейлинг-стопа
# (см. futures_position_monitor.py) ---

def register_managed_position(symbol: str, side: str, entry_price: float,
                               initial_stop_price: float, take_profit_price: float) -> None:
    """Вызывается ПОСЛЕ успешного открытия защищённой позиции (см.
    futures_signal_bridge.execute_signal/futures_testnet_demo.py) -
    сохраняет метаданные, нужные трейлинг-стопу. trail_distance
    ФИКСИРУЕТСЯ здесь один раз как |entry_price - initial_stop_price| и
    больше НИКОГДА не пересчитывается заново от текущей цены - иначе
    дистанция "плыла" бы вместе с ценой, и трейлинг перестал бы быть
    трейлингом (см. docstring futures_position_monitor.py)."""
    positions = _get("managed_positions", {})
    positions[symbol] = {
        "side": side, "entry_price": entry_price,
        "initial_stop_price": initial_stop_price, "take_profit_price": take_profit_price,
        "trail_distance": abs(entry_price - initial_stop_price),
        "opened_at": time.time(),
    }
    _set("managed_positions", positions)


def list_managed_positions() -> dict:
    """{symbol: {...метаданные...}} - все позиции, которые futures-бот
    сейчас считает "своими" и трейлит/отслеживает на закрытие."""
    return _get("managed_positions", {})


def unregister_managed_position(symbol: str) -> None:
    positions = _get("managed_positions", {})
    positions.pop(symbol, None)
    _set("managed_positions", positions)
