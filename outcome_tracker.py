"""
outcome_tracker.py - трекинг результатов опубликованных сигналов.

Проблема, которую закрывает этот модуль: бот публикует вход/стоп/тейк,
но раньше НИКОГДА не проверял, что случилось с ценой дальше. Без этого
невозможно ответить на вопрос "а сигналы вообще прибыльные?" и нечем
калибровать score/порог публикации - только "на глаз".

Логика:
1. После УСПЕШНОЙ публикации сигнала (main._publish_signal) вызывается
   record_signal_outcome(signal) - кладёт сигнал в open_outcomes
   (см. queue_manager). Специально после публикации, а не до - если
   пост не вышел, аудитория его не видела и трекать нечего.
2. На каждом тике check_open_outcomes() скачивает 15m-свечи с момента
   публикации по каждому открытому сигналу и смотрит, какой уровень
   был задет раньше - тейк или стоп (пробег по свечам в хронологическом
   порядке, см. _resolve_outcome).
3. Если прошло больше config.OUTCOME_MAX_TRACK_HOURS и ни один уровень
   не задет - закрываем как "timeout" по последней известной цене.
4. Результат уходит в closed_outcomes - на нём считается win-rate и
   средний % результата, в целом и в разбивке по стратегии/quality
   (get_accuracy_stats). Это и есть ответ на вопрос "работает ли
   формула score" - без домыслов, на фактической цене с Binance.

Намеренное упрощение: если в ОДНОЙ 15-минутной свече задеты И тейк, И
стоп одновременно, мы не знаем, что произошло раньше внутри свечи -
консервативно засчитываем это как убыток (loss), чтобы не завышать
статистику себе в пользу.
"""
import logging
import time

import requests

import config
import queue_manager

logger = logging.getLogger(__name__)

_BASE_URL = "https://data-api.binance.vision/api/v3"


def _is_short(direction: str) -> bool:
    d = direction.lower()
    return "шорт" in d or "short" in d


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def record_signal_outcome(signal) -> None:
    """Ставит только что опубликованный сигнал на трекинг результата.
    Вызывать ПОСЛЕ успешной публикации, не раньше."""
    entry_low = _to_float(signal.entry_low)
    entry_high = _to_float(signal.entry_high)
    if entry_low and entry_high:
        entry = (entry_low + entry_high) / 2
    else:
        entry = entry_low or entry_high

    stop = _to_float(signal.invalidation)
    target = _to_float(signal.target)

    if not (entry and stop and target):
        logger.warning(
            "Трекинг результата пропущен для %s - не хватает числовых уровней "
            "(entry=%s stop=%s target=%s)", signal.ticker, entry, stop, target,
        )
        return

    record = {
        "ticker": signal.ticker,
        "symbol": f"{signal.ticker}USDT",
        "direction": "short" if _is_short(signal.direction) else "long",
        "strategy": signal.strategy,
        "quality": signal.quality,
        "score": int(_to_float(signal.score)),
        "entry": entry,
        "stop": stop,
        "target": target,
        "published_at": time.time(),
    }
    queue_manager.add_open_outcome(record)
    logger.info(
        "Сигнал %s поставлен на трекинг результата (%s, entry=%.6g stop=%.6g target=%.6g)",
        record["ticker"], record["direction"], entry, stop, target,
    )


def _fetch_path_klines(symbol: str, since_ts: float) -> list[dict]:
    """15m-свечи с момента публикации по сейчас (макс. 1000 - хватает
    даже на OUTCOME_MAX_TRACK_HOURS=48ч с большим запасом: 48ч/15м=192)."""
    try:
        resp = requests.get(
            f"{_BASE_URL}/klines",
            params={
                "symbol": symbol,
                "interval": "15m",
                "startTime": int(since_ts * 1000),
                "limit": 1000,
            },
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
    except requests.RequestException as e:
        logger.debug("Не удалось получить свечи для трекинга %s: %s", symbol, e)
        return []
    return [{"high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rows]


def _resolve_outcome(record: dict, candles: list[dict]) -> tuple[str, float] | None:
    """Пробегает по свечам в хронологическом порядке, смотрит, что
    случилось раньше - тейк или стоп. None, если пока ничего не задето."""
    is_short = record["direction"] == "short"
    target, stop = record["target"], record["stop"]

    for c in candles:
        if is_short:
            hit_target = c["low"] <= target
            hit_stop = c["high"] >= stop
        else:
            hit_target = c["high"] >= target
            hit_stop = c["low"] <= stop

        if hit_target and hit_stop:
            return "loss", stop  # неизвестно, что раньше внутри свечи - консервативно засчитываем убыток
        if hit_target:
            return "win", target
        if hit_stop:
            return "loss", stop

    return None


def check_open_outcomes() -> dict:
    """Проверяет все открытые трекинги на новые результаты.
    Возвращает {"closed": N, "still_open": M} - для логов/диагностики."""
    open_items = queue_manager.get_open_outcomes()
    if not open_items:
        return {"closed": 0, "still_open": 0}

    still_open: list[dict] = []
    newly_closed: list[dict] = []
    max_age_seconds = config.OUTCOME_MAX_TRACK_HOURS * 3600

    for record in open_items:
        age = time.time() - record["published_at"]
        candles = _fetch_path_klines(record["symbol"], record["published_at"])
        resolved = _resolve_outcome(record, candles) if candles else None

        if resolved is not None:
            result, exit_price = resolved
        elif age >= max_age_seconds:
            if not candles:
                # Не удалось получить свечи вообще - не закрываем вслепую,
                # попробуем снова на следующем тике.
                still_open.append(record)
                continue
            result, exit_price = "timeout", candles[-1]["close"]
        else:
            still_open.append(record)
            continue

        sign = -1 if record["direction"] == "short" else 1
        pnl_pct = sign * (exit_price - record["entry"]) / record["entry"] * 100 if record["entry"] else 0.0

        closed = dict(record)
        closed.update({
            "result": result,
            "exit_price": exit_price,
            "pnl_pct": round(pnl_pct, 3),
            "closed_at": time.time(),
        })
        newly_closed.append(closed)
        logger.info("Результат сигнала %s: %s (%+.2f%%)", record["ticker"], result, pnl_pct)

    queue_manager.replace_open_outcomes(still_open)
    if newly_closed:
        queue_manager.append_closed_outcomes(newly_closed)

    return {"closed": len(newly_closed), "still_open": len(still_open)}


def get_accuracy_stats(days: float | None = None) -> dict:
    """Агрегированная статистика по closed_outcomes: общий win-rate и
    средний pnl%, плюс разбивка по strategy/quality. days=None - за
    всё время, иначе только записи, закрытые за последние `days` дней.

    win_rate считается только по решённым исходам (win/loss) - timeout
    не в счёт (сигнал не дошёл ни до тейка, ни до стопа, это не победа
    и не поражение стратегии как таковой), но участвует в avg_pnl_pct,
    т.к. по факту деньги там тоже "лежали" эти часы."""
    closed = queue_manager.get_closed_outcomes()
    if days is not None:
        cutoff = time.time() - days * 24 * 3600
        closed = [c for c in closed if c.get("closed_at", 0) >= cutoff]

    def _summarize(items: list[dict]) -> dict:
        n = len(items)
        if n == 0:
            return {"count": 0, "win_rate": None, "avg_pnl_pct": None}
        wins = sum(1 for c in items if c["result"] == "win")
        decided = sum(1 for c in items if c["result"] in ("win", "loss"))
        avg_pnl = sum(c["pnl_pct"] for c in items) / n
        return {
            "count": n,
            "win_rate": round(wins / decided * 100, 1) if decided else None,
            "avg_pnl_pct": round(avg_pnl, 3),
        }

    by_strategy: dict[str, list[dict]] = {}
    by_quality: dict[str, list[dict]] = {}
    for c in closed:
        by_strategy.setdefault(c.get("strategy", "?"), []).append(c)
        by_quality.setdefault(c.get("quality", "?"), []).append(c)

    return {
        "overall": _summarize(closed),
        "by_strategy": {k: _summarize(v) for k, v in by_strategy.items()},
        "by_quality": {k: _summarize(v) for k, v in by_quality.items()},
    }
