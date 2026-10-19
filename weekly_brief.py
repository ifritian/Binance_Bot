"""
Еженедельный обзор: что было на рынке и на Binance за неделю. Без LLM:
все числа берутся кодом из свечей, индекса страха и жадности, истории корзины,
журнала идей и объявлений Binance. Любой недоступный блок просто пропускается.
"""
import logging
import time

import requests

import coin_whitelist
import news_radar
import queue_manager
import research_sweep as rs

logger = logging.getLogger(__name__)

BASE_URL = "https://data-api.binance.vision/api/v3"
FNG_URL = "https://api.alternative.me/fng/?limit=8"
DAY = 86400


def fetch_week_changes(symbols: list, fetch=None) -> dict:
    """{тикер: % за 7 дней} по дневным закрытиям; монеты без данных пропускаются."""
    def _default(sym):
        r = requests.get(f"{BASE_URL}/klines", params={"symbol": f"{sym}USDT", "interval": "1d", "limit": 8}, timeout=15)
        r.raise_for_status()
        return [float(x[4]) for x in r.json()]
    out = {}
    for s in symbols:
        try:
            closes = (fetch or _default)(s)
        except (requests.RequestException, ValueError):
            continue
        if len(closes) >= 8 and closes[0] > 0:
            out[s] = (closes[-1] / closes[0] - 1) * 100
    return out


def fetch_fng_week(fetch_json=None):
    """-> (сейчас, неделю назад) или None."""
    try:
        rows = (fetch_json or (lambda: requests.get(FNG_URL, timeout=15).json()))()["data"]
        return int(rows[0]["value"]), str(rows[0]["value_classification"]), int(rows[-1]["value"])
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
        return None


def basket_week(snaps: list, now_ts: float) -> dict | None:
    recent = [s for s in snaps if now_ts - s["timestamp"] <= 8 * DAY]
    if len(recent) < 2:
        return None
    a, b = recent[0], recent[-1]

    def pct(key):
        return (b[key] / a[key] - 1) * 100 if a.get(key) and b.get(key) else None
    return {"basket": pct("index_value"), "btc": pct("btc_value"), "eth": pct("eth_value"),
            "days": (b["timestamp"] - a["timestamp"]) / DAY}


def ideas_week(closed: list, open_positions: dict, now_ts: float) -> dict:
    week = [c for c in closed if now_ts - c["ts"] <= 7 * DAY]
    return {"closed": len(week), "wins": sum(1 for c in week if c["pnl"] > 0),
            "avg": sum(c["pnl"] for c in week) / len(week) if week else 0.0, "open": len(open_positions)}


def binance_week(items: list) -> dict:
    counts = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    top = next((i for k in ("delist", "network") for i in sorted(items, key=lambda x: -x["ts"]) if i["kind"] == k), None)
    return {"counts": counts, "top": top}


def collect(now_ts: float, symbols: list | None = None, fetch_changes=fetch_week_changes,
            fetch_fng=fetch_fng_week, fetch_binance=news_radar.fetch_binance) -> dict:
    symbols = [s for s in (symbols or rs.DEFAULT_SYMBOLS) if coin_whitelist.is_allowed(s)]
    changes = fetch_changes(symbols)
    core = {k: changes.pop(k) for k in ("BTC", "ETH") if k in changes}
    ranked = sorted(changes.items(), key=lambda kv: kv[1], reverse=True)
    tb = queue_manager._get("trend_breakout_state") or {}
    lt = queue_manager._get("listing_tracker_state") or {}
    d = {
        "now": now_ts,
        "period": time.strftime("%b %d", time.gmtime(now_ts - 7 * DAY)) + " to " + time.strftime("%b %d", time.gmtime(now_ts)),
        "btc": core.get("BTC"), "eth": core.get("ETH"),
        "best": ranked[:4], "worst": [r for r in ranked[::-1] if r[1] < 0][:4],
        "basket_info": basket_week(queue_manager._get("treasury_snapshots") or [], now_ts),
        "fng": fetch_fng(),
        "ideas": ideas_week(tb.get("closed", []), tb.get("positions", {}), now_ts),
        "binance": binance_week(fetch_binance(now_ts, 7 * 24)),
        "listings": [h for h in lt.get("history", []) if now_ts - h["listing_t"] <= 14 * DAY and h.get("pct_d1") is not None],
    }
    d["basket"] = (d["basket_info"] or {}).get("basket")
    return d


def _s(p: float) -> str:
    return f"{p:+.1f}%".replace("-", "−")


def enough_data(d: dict) -> bool:
    return d["btc"] is not None and len(d["best"]) >= 2


def build_text(d: dict) -> str:
    cash = {"BTC": True}
    lines = [f"📅 Week in review ({d['period']})", ""]
    head = f"$BTC {_s(d['btc'])}"
    if d["eth"] is not None:
        head += f", ETH {_s(d['eth'])}"
    lines.append(head)
    if d["best"]:
        b = d["best"][:3]
        lines.append("Best: " + ", ".join((f"${t}" if i == 0 else t) + f" {_s(p)}" for i, (t, p) in enumerate(b)))
    if d["worst"]:
        w = d["worst"][:3]
        lines.append("Weakest: " + ", ".join((f"${t}" if i == 0 else t) + f" {_s(p)}" for i, (t, p) in enumerate(w)))
    bi = d["basket_info"]
    if bi and bi["basket"] is not None and bi["btc"] is not None:
        diff = bi["basket"] - bi["btc"]
        verb = "beat" if diff > 0.05 else ("trailed" if diff < -0.05 else "matched")
        tail = f" by {abs(diff):.1f} points" if verb != "matched" else ""
        lines.append(f"Altcoin basket {_s(bi['basket'])}: it {verb} BTC{tail} over {bi['days']:.0f} days.")
    if d["fng"]:
        now, label, then = d["fng"]
        lines.append(f"Fear & Greed: {then} a week ago, {now} now ({label}).")
    lines.append("")
    ide = d["ideas"]
    if ide["closed"]:
        lines.append(f"Trend ideas: {ide['closed']} closed this week, {ide['wins']} in profit, average {_s(ide['avg'])} after fees; {ide['open']} open.")
    else:
        lines.append(f"Trend ideas: none closed this week, {ide['open']} open.")
    bn = d["binance"]
    parts = []
    if bn["counts"].get("delist"):
        parts.append(f"{bn['counts']['delist']} delisting notice(s)")
    if bn["counts"].get("network"):
        parts.append(f"{bn['counts']['network']} network notice(s)")
    if d["listings"]:
        avg = sum(h["pct_d1"] for h in d["listings"]) / len(d["listings"])
        parts.append(f"{len(d['listings'])} new listing(s), average first day {_s(avg)}")
    if parts:
        lines.append("Binance: " + ", ".join(parts) + ".")
    lines += ["", "Which move surprised you most this week?", "", "Not financial advice.", "", "$BTC #WeeklyReview #Crypto"]
    return "\n".join(lines)
