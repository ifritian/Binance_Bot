"""
Рыночно-нейтральный недельный моментум (бумажный трекер).

Идея (проверена на истории, см. compare_strategies.py): раз в неделю ранжируем
ликвидные монеты по доходности за 28 дней, "покупаем" сильнейшую пятую часть и
"шортим" слабейшую, держим 7 дней, потом пересобираем. Рыночное направление
(рост/падение всего рынка) в основном сокращается между ногами.
На истории (4 года, 47 монет, комиссии учтены) спред был в плюсе при всех
сроках 7-56 дней, а обратная идея (реверсал) - стабильно в минусе, но
статистически результат пока не доказан (t около 1.8), список монет - сегодняшние
лидеры (смещение выжившего). Поэтому это НЕ торговля деньгами, а честный
бумажный трекер: реальные цены Binance, правила не меняются, результат каждой
недели публикуется как есть, включая убыточные.
"""
import logging
import statistics
import time

import requests

import queue_manager

logger = logging.getLogger(__name__)

BASE_URL = "https://data-api.binance.vision/api/v3"
STATE_KEY = "xsmom_state"
LOOKBACK_DAYS = 28
HOLD_DAYS = 7
FRAC = 0.2
MIN_COINS = 12
UNIVERSE_TOP = 50
COST_PCT = 0.30          # круг по обеим ногам (как в compare_strategies.py)
STABLES = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "USDTUSDT", "EURUSDT", "BUSDUSDT", "DAIUSDT", "XUSDUSDT"}
CLOSED_CAP = 200
MIN_RECORD = 4


def _state() -> dict:
    st = queue_manager._get(STATE_KEY) or {}
    st.setdefault("open", None)
    st.setdefault("closed", [])
    return st


def fetch_universe() -> list:
    try:
        rows = requests.get(f"{BASE_URL}/ticker/24hr", timeout=25).json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Моментум: нет списка монет: %s", e)
        return []
    pairs = [(r["symbol"], float(r["quoteVolume"])) for r in rows
             if r.get("symbol", "").endswith("USDT") and r["symbol"] not in STABLES
             and not any(x in r["symbol"] for x in ("UPUSDT", "DOWNUSDT", "BULL", "BEAR"))]
    pairs.sort(key=lambda p: p[1], reverse=True)
    return [s for s, _ in pairs[:UNIVERSE_TOP]]


def fetch_daily_closes(symbol: str, days: int = LOOKBACK_DAYS + 2) -> list:
    """Закрытые дневные свечи [(t_ms, close)], последняя - вчерашняя (закрытая)."""
    try:
        r = requests.get(f"{BASE_URL}/klines", params={"symbol": symbol, "interval": "1d", "limit": days}, timeout=20)
        r.raise_for_status()
        rows = [(int(k[0]), float(k[4])) for k in r.json()]
    except (requests.RequestException, ValueError, IndexError):
        return []
    now_ms = time.time() * 1000
    return [x for x in rows if x[0] + 86400_000 <= now_ms]


def fetch_prices(symbols: list) -> dict:
    try:
        rows = requests.get(f"{BASE_URL}/ticker/price", timeout=25).json()
    except (requests.RequestException, ValueError):
        return {}
    want = set(symbols)
    return {r["symbol"]: float(r["price"]) for r in rows if r.get("symbol") in want}


def rank(closes: dict, lookback: int = LOOKBACK_DAYS) -> list:
    """[(symbol, доходность за lookback дней)] по возрастанию; монеты без истории пропускаются."""
    out = []
    for s, c in closes.items():
        if len(c) > lookback and c[-1 - lookback][1] > 0:
            out.append((s, c[-1][1] / c[-1 - lookback][1] - 1))
    out.sort(key=lambda x: x[1])
    return out


def pick_legs(ranked: list, frac: float = FRAC):
    if len(ranked) < MIN_COINS:
        return None
    k = max(3, int(len(ranked) * frac))
    return ranked[-k:][::-1], ranked[:k]      # (лонг: сильнейшие первыми, шорт: слабейшие первыми)


def leg_return(legs: list, prices: dict, side: int) -> float | None:
    rets = [side * (prices[s["sym"]] / s["entry"] - 1) for s in legs if s["sym"] in prices and s["entry"] > 0]
    return statistics.mean(rets) * 100 if rets else None


def close_position(pos: dict, prices: dict, now_ts: float) -> dict | None:
    lr = leg_return(pos["long"], prices, +1)
    sr = leg_return(pos["short"], prices, -1)
    if lr is None or sr is None:
        return None
    gross = lr + sr          # sr уже со знаком шорта
    return {"t0": pos["t0"], "t1": now_ts, "long": [x["sym"] for x in pos["long"]],
            "short": [x["sym"] for x in pos["short"]], "long_pct": round(lr, 2), "short_pct": round(sr, 2),
            "gross": round(gross, 2), "net": round(gross - COST_PCT, 2)}


def tick(now_ts: float | None = None, universe=None, fetch_closes=None, fetch_px=None) -> dict | None:
    """Раз в HOLD_DAYS: закрыть прошлую корзину по текущим ценам и открыть новую.
    Возвращает {'closed': запись|None, 'opened': позиция|None, 'ranked': [...]} при событии, иначе None."""
    now_ts = now_ts or time.time()
    fetch_closes = fetch_closes or fetch_daily_closes
    fetch_px = fetch_px or fetch_prices
    st = _state()
    pos = st["open"]
    if pos and now_ts - pos["t0"] < HOLD_DAYS * 86400:
        return None
    universe = universe if universe is not None else fetch_universe()
    if not universe:
        return None
    closes = {s: fetch_closes(s) for s in universe}
    ranked = rank({s: c for s, c in closes.items() if c})
    legs = pick_legs(ranked)
    if legs is None:
        return None
    longs, shorts = legs
    syms = [s for s, _ in longs + shorts] + ([x["sym"] for x in pos["long"] + pos["short"]] if pos else [])
    prices = fetch_px(list(dict.fromkeys(syms)))
    event = {"closed": None, "opened": None, "ranked": ranked}
    if pos:
        rec = close_position(pos, prices, now_ts)
        if rec:
            st["closed"].append(rec)
            st["closed"] = st["closed"][-CLOSED_CAP:]
            event["closed"] = rec
    if not all(s in prices for s, _ in longs + shorts):
        queue_manager._set(STATE_KEY, st)
        return event if event["closed"] else None
    new = {"t0": now_ts,
           "long": [{"sym": s, "entry": prices[s], "ret28": r} for s, r in longs],
           "short": [{"sym": s, "entry": prices[s], "ret28": r} for s, r in shorts]}
    st["open"] = new
    queue_manager._set(STATE_KEY, st)
    event["opened"] = new
    return event


def stats() -> dict:
    cl = _state()["closed"]
    nets = [c["net"] for c in cl]
    return {"n": len(nets), "wins": sum(1 for x in nets if x > 0),
            "avg": round(statistics.mean(nets), 2) if nets else 0.0, "sum": round(sum(nets), 1)}


def record_line(s: dict | None = None) -> str:
    s = s or stats()
    if s["n"] < MIN_RECORD:
        return f"Paper record so far: {s['n']} closed weeks. Every week is logged, wins and losses."
    return (f"Paper record: {s['wins']}/{s['n']} weeks in profit, avg {s['avg']:+.2f}% a week after costs, "
            f"{s['sum']:+.1f}% in total.")


def _tk(sym: str) -> str:
    return sym.removesuffix("USDT")


def build_text(event: dict, s: dict | None = None) -> str:
    """Пост: прошлая неделя + новая корзина. Не более 3 различных $тикеров."""
    new = event["opened"]
    longs = [x["sym"] for x in new["long"]]
    shorts = [x["sym"] for x in new["short"]]
    r28 = {x["sym"]: x["ret28"] for x in new["long"] + new["short"]}

    def fmt(sym, cash):
        t = _tk(sym)
        return f"{'$' if cash else ''}{t} {r28[sym] * 100:+.0f}%".replace("-", "−")
    lines = ["Momentum board: strongest and weakest coins of the last 28 days", ""]
    lines.append("Strongest: " + ", ".join(fmt(x, i < 2) for i, x in enumerate(longs[:4])))
    lines.append("Weakest: " + ", ".join(fmt(x, i < 1) for i, x in enumerate(shorts[:4])))
    c = event.get("closed")
    if c:
        lines += ["", f"Last week's paper long/short basket: {c['net']:+.2f}% after costs "
                      f"(longs {c['long_pct']:+.1f}%, shorts {c['short_pct']:+.1f}%)."]
    lines += ["", record_line(s), "Paper test of a market-neutral idea, not a recommendation.",
              "", f"#{_tk(longs[0])} #Crypto"]
    return "\n".join(lines)
