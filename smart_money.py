"""Наблюдение за идеей "крупные игроки знают больше" (без публикаций и без торговли).

Раз в сутки для самых ликвидных монет берём бесплатные дневные файлы data.binance.vision (позиции и счета топ-трейдеров,
все счета, открытый интерес, агрессивные покупки/продажи), считаем те же признаки, что в бэктесте (smart_money_research.py),
и записываем событие, когда признак отклонился от нормы на |z| >= 2 (не чаще раза в 12 часов на монету и признак). Позже к
событию дописывается результат: что сделала цена через 4/12/24/48 часов минус BTC (убираем движение всего рынка).
На странице Receipts виден итог по каждому признаку: "в сторону" или "против".
Почему файлы, а не API: фьючерсное API Binance из GitHub Actions недоступно (ошибка 451 из-за региона), а файлы и
зеркало цен data-api.binance.vision доступны. Платим за это задержкой в сутки, для наблюдения она не мешает.
Это наблюдение, не торговое правило: пока выборка мала, любые цифры - шум."""
import logging
import statistics

import requests

import queue_manager
import scanner
import smart_money_research as smr

logger = logging.getLogger(__name__)

UNIVERSE = 15
THRESHOLD = 2.0
COOLDOWN_H = 12
MAX_EVENTS = 4000
HIST_DAYS = 12
H = 3600_000
HORIZONS = smr.HORIZONS
COST = smr.COST
EXCLUDE = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BTCDOMUSDT"}


def fetch_bars(symbol: str, limit: int = 500) -> list:
    try:
        r = requests.get(f"{scanner._BASE_URL}/klines", params={"symbol": symbol, "interval": "1h", "limit": limit}, timeout=20)
        r.raise_for_status()
        return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in r.json()]
    except (requests.RequestException, ValueError) as e:
        logger.warning("Smart money: свечи %s недоступны: %s", symbol, e)
        return []


def fetch_hm(symbol: str, days: int = HIST_DAYS, today=None) -> dict:
    """Часовые признаки за последние `days` суток из дневных файлов (последний файл - вчерашний)."""
    from datetime import datetime, timedelta, timezone
    end = (today or datetime.now(timezone.utc).date()) - timedelta(days=1)
    hm = {}
    for k in range(days):
        hm.update(smr.hourly_metrics(smr.fetch_metrics_day(symbol, (end - timedelta(days=k)).isoformat())))
    return hm


def universe(rows: list) -> list:
    out = [(float(r["quoteVolume"]), r["symbol"]) for r in rows or []
           if r.get("symbol", "").endswith("USDT") and r["symbol"] not in EXCLUDE and r["symbol"].isascii()]
    return [s for _v, s in sorted(out, reverse=True)[:UNIVERSE]]


def _ret(bars: list, t_event: int, h: int, bidx: dict):
    i, j = bidx.get(t_event), bidx.get(t_event + h * H)
    return (bars[j][4] / bars[i][4] - 1) * 100 if i is not None and j is not None else None


def fill_outcomes(events: list, symbol: str, bars: list, btc: list) -> None:
    bi_, bb = {b[0]: k for k, b in enumerate(bars)}, {b[0]: k for k, b in enumerate(btc)}
    for ev in events:
        if ev["s"] != symbol:
            continue
        out = ev.setdefault("out", {})
        for h in HORIZONS:
            if str(h) in out:
                continue
            r = _ret(bars, ev["t"], h, bi_)
            if r is None:
                continue
            b = _ret(btc, ev["t"], h, bb) if symbol != "BTCUSDT" else 0.0
            if b is not None:
                out[str(h)] = [round(r, 3), round(b, 3)]


def new_events(symbol: str, bars: list, hm: dict, events: list, since_t: int = 0) -> list:
    """События по закрытым свечам, для которых есть признаки и время > since_t."""
    if len(bars) < smr.Z_WINDOW + 10 or not hm:
        return []
    z = smr.build_features(bars[:-1], hm)                         # последняя свеча ещё идёт
    mine = [e for e in events if e["s"] == symbol]
    out = []
    for i in range(smr.Z_WINDOW, len(bars) - 1):
        t = bars[i][0]
        if t <= since_t:
            continue
        for f in smr.FEATURES:
            v = z[f][i]
            if v is None or abs(v) < THRESHOLD:
                continue
            if any(e["f"] == f and abs(t - e["t"]) < COOLDOWN_H * H for e in mine + out):
                continue
            out.append({"t": t, "s": symbol, "f": f, "z": round(v, 2), "px": bars[i][4]})
    return out


def scan(rows: list, now: float | None = None) -> int:
    """Суточный проход: новые события и результаты старых. -> число новых событий."""
    events = queue_manager._get("sm_events") or []
    since = max((e["t"] for e in events), default=0)
    syms = universe(rows)
    if "BTCUSDT" not in syms:
        syms = ["BTCUSDT"] + syms
    btc = fetch_bars("BTCUSDT")
    added = 0
    for s in syms:
        bars = btc if s == "BTCUSDT" else fetch_bars(s)
        if not bars:
            continue
        hm = fetch_hm(s)
        for ev in new_events(s, bars, hm, events, since):
            events.append(ev)
            added += 1
        fill_outcomes(events, s, bars, btc)
    queue_manager._set("sm_events", events[-MAX_EVENTS:])
    logger.info("Smart money: новых событий %d, всего %d", added, len(events))
    return added


def stats(events: list) -> list:
    """По (признак, режим, часов): n, средний excess к BTC %, net после комиссии %, доля плюсовых.
    follow = в сторону отклонения z, fade = против."""
    acc = {}
    for ev in events:
        sd = 1 if ev["z"] > 0 else -1
        for h, (r, b) in (ev.get("out") or {}).items():
            for mode, side in (("follow", sd), ("fade", -sd)):
                acc.setdefault((ev["f"], mode, int(h)), []).append((side * (r - b), side * r - COST))
    rows = []
    for (f, mode, h), v in acc.items():
        ex = [x for x, _n in v]
        rows.append({"f": f, "mode": mode, "h": h, "n": len(v), "excess": statistics.mean(ex), "net": statistics.mean(n for _x, n in v),
                     "hit": sum(1 for x in ex if x > 0) / len(v)})
    rows.sort(key=lambda r: (-r["n"], r["f"], r["mode"], r["h"]))
    return rows


def run_if_due(rows: list | None, utc_hour: int | None = None) -> None:
    """Из тика: раз в сутки после 03:00 UTC (дневные файлы публикуются ночью)."""
    from datetime import datetime, timezone
    hour = datetime.now(timezone.utc).hour if utc_hour is None else utc_hour
    if not rows or hour < 3 or queue_manager.seconds_since_last_post("sm_scan") < 20 * 3600:
        return
    queue_manager.set_last_post_time("sm_scan")
    try:
        scan(rows)
    except Exception:
        logger.exception("Smart money: сбой прохода")
