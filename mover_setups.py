"""
"Сетап по движущейся монете": когда ликвидная монета резко идёт в одну сторону,
бот публикует пост с конкретным планом - вход, стоп, цель - и ЧЕСТНО считает
итог каждой такой идеи (цель / стоп / 24 часа), включая убыточные.

Правила идеи (одни и те же всегда, ничего не подгоняется):
  - сторона = направление движения за 24ч (momentum);
  - стоп = 1.5 * ATR(14) на часовых свечах (но не ближе 1.0% от входа);
  - цель = 2 * расстояние до стопа (риск/прибыль 1:2);
  - идея живёт 24 часа; если и стоп, и цель задеты в одной 15-минутной свече -
    считаем стоп (консервативно); комиссия 0.15% за круг вычитается из итога.
Это не торговая рекомендация и не доказанная стратегия - идея просто
публикуется и проверяется открыто. Итоговый счёт идёт в каждый следующий пост.
"""
import logging
import time

import requests

import queue_manager

logger = logging.getLogger(__name__)

BASE_URL = "https://data-api.binance.vision/api/v3"
STATE_KEY = "mover_setups"
ATR_N = 14
STOP_ATR = 1.5
MIN_STOP_PCT = 1.0
RR = 2.0
HOLD_S = 24 * 3600
COST_PCT = 0.15
CLOSED_CAP = 300
MIN_RECORD = 5   # столько закрытых идей нужно, чтобы показывать счёт в постах


def _state() -> dict:
    st = queue_manager._get(STATE_KEY) or {}
    st.setdefault("open", [])
    st.setdefault("closed", [])
    return st


def fetch_klines(symbol: str, interval: str, limit: int, start_ms: int | None = None) -> list:
    """[(t, o, h, l, c)] ; [] при ошибке."""
    params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}
    if start_ms:
        params["startTime"] = int(start_ms)
    try:
        r = requests.get(f"{BASE_URL}/klines", params=params, timeout=20)
        r.raise_for_status()
        return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in r.json()]
    except (requests.RequestException, ValueError, IndexError) as e:
        logger.warning("Сетап: нет свечей %s %s: %s", symbol, interval, e)
        return []


def atr(bars: list, n: int = ATR_N) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        _, _, h, l, _ = bars[i]
        pc = bars[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


def build_setup(ticker: str, price: float, pct24: float, bars_1h: list) -> dict | None:
    a = atr(bars_1h)
    if a is None or price <= 0 or pct24 == 0:
        return None
    side = "long" if pct24 > 0 else "short"
    dist = max(STOP_ATR * a, price * MIN_STOP_PCT / 100)
    if side == "long":
        stop, target = price - dist, price + RR * dist
    else:
        stop, target = price + dist, price - RR * dist
    if stop <= 0 or target <= 0:
        return None
    return {"ticker": ticker, "side": side, "entry": price, "stop": stop, "target": target,
            "pct24": pct24, "dist_pct": dist / price * 100}


def register(setup: dict, now_ts: float | None = None) -> None:
    st = _state()
    s = dict(setup)
    s["t0"] = now_ts or time.time()
    s["posted_result"] = False
    st["open"].append(s)
    queue_manager._set(STATE_KEY, st)


def _net(side: str, entry: float, exit_: float) -> float:
    raw = (exit_ - entry) / entry * 100
    if side == "short":
        raw = -raw
    return round(raw - COST_PCT, 2)


def evaluate_one(pos: dict, bars: list, now_ts: float) -> dict | None:
    """Результат {'outcome','exit','net'} либо None, если идея ещё открыта."""
    long = pos["side"] == "long"
    for t, o, h, l, c in bars:
        if t < pos["t0"] * 1000 - 15 * 60 * 1000:
            continue
        hit_stop = l <= pos["stop"] if long else h >= pos["stop"]
        hit_tp = h >= pos["target"] if long else l <= pos["target"]
        if hit_stop:
            return {"outcome": "stop", "exit": pos["stop"], "net": _net(pos["side"], pos["entry"], pos["stop"])}
        if hit_tp:
            return {"outcome": "target", "exit": pos["target"], "net": _net(pos["side"], pos["entry"], pos["target"])}
    if now_ts - pos["t0"] >= HOLD_S:
        last = bars[-1][4] if bars else pos["entry"]
        return {"outcome": "time", "exit": last, "net": _net(pos["side"], pos["entry"], last)}
    return None


def advance(now_ts: float | None = None, fetch=None) -> None:
    now_ts = now_ts or time.time()
    fetch = fetch or fetch_klines
    st = _state()
    still = []
    for pos in st["open"]:
        bars = fetch(pos["ticker"], "15m", 200, int(pos["t0"] * 1000))
        res = evaluate_one(pos, bars, now_ts) if bars else None
        if res is None:
            if now_ts - pos["t0"] > HOLD_S + 6 * 3600:   # данные так и не получили - не копим
                continue
            still.append(pos)
            continue
        done = dict(pos, **res, closed_t=now_ts)
        st["closed"].append(done)
    st["open"] = still
    st["closed"] = st["closed"][-CLOSED_CAP:]
    queue_manager._set(STATE_KEY, st)


def pending_result():
    """Закрытая идея, по которой ещё не было поста об итоге (свежее 12ч)."""
    now = time.time()
    for c in _state()["closed"]:
        if not c.get("posted_result") and now - c.get("closed_t", 0) < 12 * 3600:
            return c
    return None


def mark_result_posted(closed_t: float) -> None:
    st = _state()
    for c in st["closed"]:
        if c.get("closed_t") == closed_t:
            c["posted_result"] = True
    queue_manager._set(STATE_KEY, st)


def stats() -> dict:
    st = _state()
    cl = st["closed"]
    wins = sum(1 for c in cl if c["net"] > 0)
    avg = round(sum(c["net"] for c in cl) / len(cl), 2) if cl else 0.0
    return {"closed": len(cl), "open": len(st["open"]), "wins": wins, "avg_net": avg}


def _px(x: float) -> str:
    if x >= 1000:
        return f"{x:,.0f}".replace(",", " ")
    if x >= 1:
        return f"{x:.2f}"
    if x >= 0.01:
        return f"{x:.4f}"
    return f"{x:.6f}"


def record_line(s: dict | None = None) -> str:
    s = s or stats()
    if s["closed"] < MIN_RECORD:
        return f"Track record: {s['closed']} closed so far, every idea is logged, wins and losses."
    return (f"Track record: {s['wins']}/{s['closed']} ideas ended in profit, "
            f"avg {s['avg_net']:+.2f}% per idea after fees.")


def build_text(setup: dict, s: dict | None = None) -> str:
    t, side = setup["ticker"], setup["side"]
    up = setup["pct24"] > 0
    head = (f"{'🚀' if up else '🔻'} ${t} {setup['pct24']:+.1f}% in 24h. "
            f"Momentum {side} idea:")
    risk = setup["dist_pct"]
    tp_sign = "+" if side == "long" else "-"
    st_sign = "-" if side == "long" else "+"
    lines = [
        head, "",
        f"Entry: {_px(setup['entry'])}",
        f"Stop: {_px(setup['stop'])} ({st_sign}{risk:.1f}%)",
        f"Target: {_px(setup['target'])} ({tp_sign}{risk * RR:.1f}%)",
        "Risk/reward 1:2. Idea expires after 24h.", "",
        record_line(s), "Not financial advice.", "",
        f"#{t} #Crypto",
    ]
    return "\n".join(lines)


_OUT = {"target": "hit the target ✅", "stop": "was stopped out ❌", "time": "closed after 24h"}


def build_result_text(c: dict, s: dict | None = None) -> str:
    return "\n".join([
        f"${c['ticker']} {c['side']} idea {_OUT[c['outcome']]}: {c['net']:+.2f}% after fees.", "",
        f"Entry {_px(c['entry'])} → exit {_px(c['exit'])}.",
        record_line(s), "", f"#{c['ticker']} #Crypto",
    ])
