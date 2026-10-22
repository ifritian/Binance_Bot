"""Рост аудитории: число подписчиков Telegram по дням и путь к порогу монетизации (1000 подписчиков -
50% дохода от рекламы в канале, по правилам Telegram). Только чтение/запись счётчика, ничего не публикуется."""
import time

import queue_manager

KEY = "audience_log"
TELEGRAM_GOAL = 1000


def record(count: int, ts: float | None = None) -> list:
    """Одна запись в сутки: [[день, число]]; хранится последние 120 дней."""
    day = time.strftime("%Y-%m-%d", time.gmtime(ts or time.time()))
    log = [e for e in (queue_manager._get(KEY) or []) if e[0] != day]
    log.append([day, int(count)])
    log = log[-120:]
    queue_manager._set(KEY, log)
    return log


def summary(log: list, goal: int = TELEGRAM_GOAL) -> str:
    if not log:
        return "Telegram: нет данных о подписчиках"
    day, n = log[-1]
    week = next((c for d, c in log if d >= time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))), None)
    delta = f", {n - week:+d} за неделю" if week is not None and len(log) > 1 else ""
    left = f"до {goal} осталось {goal - n}" if n < goal else f"порог {goal} пройден"
    return f"Telegram: {n} подписчиков{delta}, {left}"
