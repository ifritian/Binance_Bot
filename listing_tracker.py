"""
Listing Tracker: что стало с монетой после листинга на Binance.

Для каждого анонса "Binance Will List X (X)":
1. ждём первую свечу пары X/USDT (момент начала торгов) и запоминаем цену
   открытия первой минуты;
2. через 24 часа торгов - отчёт: где цена относительно первого открытия,
   диапазон за сутки, объём;
3. через 7 дней - второй отчёт: что осталось от движения.

Все числа берутся из свечей Binance кодом, текст по шаблону (без LLM).
В каждом отчёте - общая статистика по всем отслеженным листингам (с честной
оговоркой про малую выборку). Состояние - queue_manager._get/_set.
"""
import logging
import statistics

import requests

import queue_manager

logger = logging.getLogger(__name__)

STATE_KEY = "listing_tracker_state"
BASE_URL = "https://data-api.binance.vision/api/v3"
DAY = 86400
D1_AFTER_S = DAY + 600
D7_AFTER_S = 7 * DAY + 600
EXPIRE_WAIT_S = 10 * DAY          # пара так и не появилась - забываем
EVENT_TTL_S = 24 * 3600           # отчёт не публикуем, если он устарел
HISTORY_CAP = 300
TRACKED_CAP = 60


def _state() -> dict:
    st = queue_manager._get(STATE_KEY) or {}
    st.setdefault("tracked", {})
    st.setdefault("history", [])
    st.setdefault("outbox", [])
    return st


def fetch_klines(symbol: str, interval: str, start_ms: int, limit: int):
    """Строки свечей или None при ошибке сети (пустой список = пары ещё нет)."""
    try:
        r = requests.get(f"{BASE_URL}/klines", params={"symbol": symbol, "interval": interval,
                                                       "startTime": start_ms, "limit": limit}, timeout=15)
        if r.status_code == 400:
            return []  # неизвестный символ
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Listing tracker: свечи %s недоступны: %s", symbol, e)
        return None


def register(items: list, now_ts: float) -> int:
    """items - [{"code","name","ticker","ts"}] из event_posts.fetch_listing_announcements."""
    st = _state()
    seen = set(st["tracked"]) | {h["code"] for h in st["history"]}
    added = 0
    for a in items:
        if a["code"] in seen or len(st["tracked"]) >= TRACKED_CAP:
            continue
        st["tracked"][a["code"]] = {"ticker": a["ticker"], "name": a["name"], "ann_ts": a["ts"], "stage": "wait"}
        added += 1
    if added:
        queue_manager._set(STATE_KEY, st)
    return added


def _window_stats(rows: list, open_price: float) -> dict | None:
    if not rows or open_price <= 0:
        return None
    closes = [float(r[4]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    return {
        "open": open_price, "close": closes[-1],
        "pct_close": (closes[-1] / open_price - 1) * 100,
        "pct_high": (max(highs) / open_price - 1) * 100,
        "pct_low": (min(lows) / open_price - 1) * 100,
        "quote_volume": sum(float(r[7]) for r in rows),
        "closes": closes,
    }


def advance(now_ts: float, fetch=fetch_klines) -> list:
    """Двигает отслеживаемые листинги по стадиям; возвращает новые события-отчёты."""
    st = _state()
    new_events = []
    for code, e in list(st["tracked"].items()):
        sym = f"{e['ticker']}USDT"
        if e["stage"] == "wait":
            rows = fetch(sym, "1m", int((e["ann_ts"] - 3600) * 1000), 1)
            if rows is None:
                continue
            if not rows:
                if now_ts - e["ann_ts"] > EXPIRE_WAIT_S:
                    e["stage"] = "expired"
                continue
            t0 = rows[0][0] / 1000.0
            if t0 < e["ann_ts"] - 300:
                e["stage"] = "skip"   # пара торговалась и до анонса - это не новый листинг
                continue
            e.update(stage="d1", listing_t=t0, open=float(rows[0][1]))
        if e["stage"] == "d1" and now_ts >= e["listing_t"] + D1_AFTER_S:
            rows = fetch(sym, "1h", int(e["listing_t"] * 1000), 24)
            stats = _window_stats(rows or [], e["open"])
            if stats and len(rows) >= 20:
                e.update(stage="d7", d1=stats["pct_close"])
                st["outbox"].append({"id": f"{code}:d1", "kind": "d1", "ticker": e["ticker"], "name": e["name"],
                                     "listing_t": e["listing_t"], "due_ts": now_ts, "stats": stats, "posted": False})
                new_events.append(st["outbox"][-1])
        if e["stage"] == "d7" and now_ts >= e["listing_t"] + D7_AFTER_S:
            rows = fetch(sym, "1h", int(e["listing_t"] * 1000), 168)
            stats = _window_stats(rows or [], e["open"])
            if stats and len(rows) >= 150:
                stats["pct_d1"] = e.get("d1")
                e["stage"] = "done"
                st["history"].append({"code": code, "ticker": e["ticker"], "listing_t": e["listing_t"],
                                      "open": e["open"], "pct_d1": e.get("d1"), "pct_d7": stats["pct_close"]})
                st["outbox"].append({"id": f"{code}:d7", "kind": "d7", "ticker": e["ticker"], "name": e["name"],
                                     "listing_t": e["listing_t"], "due_ts": now_ts, "stats": stats, "posted": False})
                new_events.append(st["outbox"][-1])
    # уборка
    st["tracked"] = {c: e for c, e in st["tracked"].items()
                     if e["stage"] not in ("expired", "skip", "done") or now_ts - e.get("listing_t", e["ann_ts"]) < 30 * DAY}
    st["history"] = st["history"][-HISTORY_CAP:]
    st["outbox"] = [ev for ev in st["outbox"] if now_ts - ev["due_ts"] < 3 * DAY][-40:]
    queue_manager._set(STATE_KEY, st)
    return new_events


def pending_event(now_ts: float) -> dict | None:
    for ev in _state()["outbox"]:
        if not ev["posted"] and now_ts - ev["due_ts"] <= EVENT_TTL_S:
            return ev
    return None


def mark_posted(event_id: str) -> None:
    st = _state()
    for ev in st["outbox"]:
        if ev["id"] == event_id:
            ev["posted"] = True
    queue_manager._set(STATE_KEY, st)


def summary(kind: str = "d1") -> dict | None:
    """Статистика по всем отслеженным листингам. Для d1 учитываем и те, что
    ещё ждут недельный отчёт; хранится в history только после d7, поэтому d1
    берём из tracked тоже."""
    st = _state()
    key = "pct_d1" if kind == "d1" else "pct_d7"
    vals = [h[key] for h in st["history"] if h.get(key) is not None]
    if kind == "d1":
        vals += [e["d1"] for e in st["tracked"].values() if e.get("d1") is not None and e["stage"] == "d7"]
    if not vals:
        return None
    return {"n": len(vals), "avg": sum(vals) / len(vals), "median": statistics.median(vals),
            "up": sum(1 for v in vals if v > 0)}


# ---------------- тексты ----------------

def _s(p: float) -> str:
    return f"{p:+.1f}%".replace("-", "−")


def _money(v: float) -> str:
    # без знака доллара: Square может принять "$48M" за тикер
    return f"{v / 1e9:.1f}B USDT" if v >= 1e9 else (f"{v / 1e6:.1f}M USDT" if v >= 1e6 else f"{v / 1e3:.0f}K USDT")


def _price(p: float) -> str:
    return f"{p:,.2f}" if p >= 1 else f"{p:.6g}"


def build_text(ev: dict, summ: dict | None) -> str:
    s, t = ev["stats"], ev["ticker"]
    if ev["kind"] == "d1":
        head = f"📊 Listing report: ${t}, 24 hours after trading started on Binance"
        body = [f"First price {_price(s['open'])}, now {_price(s['close'])} ({_s(s['pct_close'])}).",
                f"24h range from the first price: {_s(s['pct_low'])} to {_s(s['pct_high'])}.",
                f"Traded volume: {_money(s['quote_volume'])}."]
        q = "Do new listings still pump for you, or is the move usually over by day two?"
    else:
        head = f"📊 One week later: ${t}, 7 days after listing on Binance"
        d1 = s.get("pct_d1")
        body = [f"First price {_price(s['open'])}, now {_price(s['close'])} ({_s(s['pct_close'])})."]
        if d1 is not None:
            body.append(f"After day one it was {_s(d1)}; the week range was {_s(s['pct_low'])} to {_s(s['pct_high'])}.")
        else:
            body.append(f"Week range from the first price: {_s(s['pct_low'])} to {_s(s['pct_high'])}.")
        q = "Is holding a new listing for a week worth it, or do you take profit on day one?"
    out = [head, "", *body]
    if summ and summ["n"] >= 2:
        label = "24h" if ev["kind"] == "d1" else "7-day"
        out += ["", f"All {summ['n']} Binance listings we tracked: average {label} move {_s(summ['avg'])}, "
                    f"median {_s(summ['median'])}, {summ['up']} of {summ['n']} ended higher. "
                    "Small sample, not a forecast."]
    out += ["", q, "", "Not financial advice.", "", f"${t} #Binance #NewListing"]
    return "\n".join(out)
