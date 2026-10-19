"""
Независимый ряд "корзина альткоинов против BTC и ETH" для публичной страницы и
недельного обзора. Раз в 6 часов считает изменение корзины (тот же состав и
веса, что у Treasury Index) и BTC/ETH за прошедшее время и накапливает
кумулятивные значения с базой 100. Посты про корзину этот ряд не трогает:
у них свой счётчик (queue_manager.update_treasury_history).
"""
import logging
import math

import queue_manager

logger = logging.getLogger(__name__)

STATE_KEY = "basket_track"
INTERVAL_S = 6 * 3600
MAX_GAP_H = 24
MAX_POINTS = 700


def _default_compute(period_hours: int):
    """-> (индекс %, BTC %, ETH %) за период; None, если корзину посчитать не удалось."""
    import treasury_index
    res = treasury_index.compute_index(period_hours)
    if res is None or res.total_pct is None:
        return None
    btc = treasury_index.fetch_reference_change_pct("BTCUSDT", period_hours)
    eth = treasury_index.fetch_reference_change_pct("ETHUSDT", period_hours)
    if btc is None:
        return None
    return res.total_pct, btc, eth


def points() -> list:
    return (queue_manager._get(STATE_KEY) or {}).get("points", [])


def tick(now_ts: float, compute=_default_compute) -> bool:
    """True, если записана новая точка."""
    st = queue_manager._get(STATE_KEY) or {"points": []}
    pts = st["points"]
    if not pts:
        pts.append({"timestamp": now_ts, "index_value": 100.0, "btc_value": 100.0, "eth_value": 100.0})
        queue_manager._set(STATE_KEY, st)
        return True
    last = pts[-1]
    elapsed = now_ts - last["timestamp"]
    if elapsed < INTERVAL_S:
        return False
    hours = min(max(math.ceil(elapsed / 3600), 1), MAX_GAP_H)
    got = compute(hours)
    if got is None:
        return False
    idx, btc, eth = got
    pts.append({
        "timestamp": now_ts,
        "index_value": round(last["index_value"] * (1 + idx / 100), 4),
        "btc_value": round(last["btc_value"] * (1 + btc / 100), 4),
        "eth_value": round(last["eth_value"] * (1 + (eth if eth is not None else 0.0) / 100), 4),
    })
    st["points"] = pts[-MAX_POINTS:]
    queue_manager._set(STATE_KEY, st)
    return True


def series() -> list:
    """Ряд для страницы: свой, а если он ещё короткий - старые снимки Treasury."""
    own = points()
    return own if len(own) >= 2 else (queue_manager._get("treasury_snapshots") or [])
