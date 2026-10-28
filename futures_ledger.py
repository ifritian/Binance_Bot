"""Публичный журнал демо-фьючерсов (Binance Futures TESTNET): деньги и исполнение игровые, не реальные.

Считаем только то, что записано ботом: закрытые сделки и их реализованный результат в USDT. Старые записи без цены выхода
получают причину закрытия, восстановленную по PnL (помечена). Ничего не округляется в пользу бота.
"""
import time

import queue_manager

SINCE_KEY = "futures_enabled_since"


def implied_exit(rec: dict) -> float | None:
    q, e, p = rec.get("original_quantity") or rec.get("quantity"), rec.get("entry_price"), rec.get("realized_pnl")
    if not q or not e or p is None:
        return None
    return e + p / q if str(rec.get("side", "BUY")).upper() == "BUY" else e - p / q


def reason(rec: dict) -> str:
    r = str(rec.get("close_reason", ""))
    if not r.startswith("неизвестно"):
        return {"стоп-лосс (SL)": "Stop", "тейк-профит (TP)": "Target"}.get(r, "Trailing / other")
    px = implied_exit(rec)
    if px is None:
        return "Unknown"
    import futures_position_monitor as fpm
    c = fpm.classify_exit(rec, px)
    return {"стоп-лосс (SL)": "Stop (derived)", "тейк-профит (TP)": "Target (derived)"}.get(c, "Between stop and target (derived)")


def summary(closed: list, since_ts: float | None = None) -> dict:
    rows = [c for c in closed if since_ts is None or (c.get("closed_at") or 0) >= since_ts]
    pn = [c["realized_pnl"] for c in rows if c.get("realized_pnl") is not None]
    return {"n": len(pn), "wins": sum(1 for p in pn if p > 0), "total": sum(pn), "avg": (sum(pn) / len(pn)) if pn else 0.0,
            "best": max(pn, default=0.0), "worst": min(pn, default=0.0)}


def mark_enabled(now: float | None = None) -> None:
    if not queue_manager._get(SINCE_KEY):
        queue_manager._set(SINCE_KEY, now or time.time())


def week_line(closed: list, now_ts: float) -> str | None:
    since = queue_manager._get(SINCE_KEY)
    if not since:
        return None
    s = summary([c for c in closed if (c.get("closed_at") or 0) >= now_ts - 7 * 86400], since)
    if not s["n"]:
        return "Demo futures account: no trades closed this week."
    return (f"Demo futures account (play money): {s['n']} closed this week, {s['wins']} in profit, "
            f"net {s['total']:+.0f} USDT.").replace("-", "−", 1) if s["total"] < 0 else \
        f"Demo futures account (play money): {s['n']} closed this week, {s['wins']} in profit, net {s['total']:+.0f} USDT."
