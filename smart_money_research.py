#!/usr/bin/env python3
"""Проверка идеи "крупные игроки знают больше": предсказывают ли позиции топ-трейдеров Binance Futures (и открытый интерес,
и агрессивные покупки/продажи) движение цены в следующие часы?

Данные - бесплатные дневные файлы data.binance.vision (futures/um/daily/metrics, шаг 5 минут): соотношение лонг/шорт по
позициям и по счетам ТОП-трейдеров (крупных), по всем счетам, суммарный открытый интерес, соотношение агрессивных покупок
к продажам. Цена - часовые свечи. Признак считается на закрытии часа, сделка условная: вход по закрытию, выход через
h часов, комиссия 0.15% круг.

Защита от самообмана:
- z-оценка признака считается только по прошлому (7 суток), признак известен на закрытии свечи - заглядывания вперёд нет;
- вход не чаще раза в h часов на монету;
- результат сравнивается с безусловным средним (дрейф рынка не считается преимуществом): "excess";
- значимость считается по кластерам дней (монеты в один день двигаются вместе): t по дням, а не по сделкам;
- конфигураций много, поэтому порог t = 3.5 (поправка на множественные проверки), плюс обе половины истории в плюсе, n >= 200;
- список монет - сегодняшний топ по обороту (смещение выжившего).
Итог - гипотеза для живого наблюдения (smart_money.py), не торговое правило."""
import argparse
import csv
import io
import math
import statistics
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

import backtest_ideas as bi
import scanner

COST = 0.15
HORIZONS = (4, 12, 24, 48)
THRESHOLDS = (1.5, 2.0, 2.5)
Z_WINDOW = 168
VISION = "https://data.binance.vision/data/futures/um/daily/metrics/{s}/{s}-metrics-{d}.zip"
FEATURES = ("tt_pos", "tt_acc", "retail", "div", "taker", "d_tt_pos", "oi_chg", "oi_price_up", "oi_price_dn")


def fetch_metrics_day(symbol: str, day: str) -> list:
    """Строки CSV одного дня (список словарей) или [] если файла нет."""
    for attempt in range(3):
        try:
            r = requests.get(VISION.format(s=symbol, d=day), timeout=30)
            if r.status_code == 404:
                return []
            r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            return list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8")))
        except (requests.RequestException, zipfile.BadZipFile):
            time.sleep(1 + attempt)
    return []


def hourly_metrics(rows: list) -> dict:
    """{час_открытия_мс: признаки на закрытии часа}: берём последнюю строку со временем <= закрытия часа."""
    out = {}
    for r in rows:
        try:
            ts = int(datetime.strptime(r["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
            vals = (float(r["sum_open_interest_value"]), float(r["sum_toptrader_long_short_ratio"]), float(r["count_toptrader_long_short_ratio"]),
                    float(r["count_long_short_ratio"]), float(r["sum_taker_long_short_vol_ratio"]))
        except (KeyError, ValueError):
            continue
        hour_open = (ts - 1) // 3600_000 * 3600_000        # строка в hh:00 относится к закрывшемуся часу
        out[hour_open] = vals                               # более поздние строки часа перезаписывают ранние
    return out


def zscore(series: list, i: int, win: int = Z_WINDOW):
    if i < win:
        return None
    w = series[i - win:i]
    if any(v is None for v in w) or series[i] is None:
        return None
    sd = statistics.pstdev(w)
    return (series[i] - statistics.mean(w)) / sd if sd > 1e-12 else None


def build_features(bars: list, hm: dict) -> dict:
    """-> {имя признака: [z на каждом баре или None]} для сигналов; значение + направление 'высокий z' заданы в сигнале."""
    n = len(bars)
    oi = [hm.get(b[0], (None,) * 5)[0] for b in bars]
    pos = [hm.get(b[0], (None,) * 5)[1] for b in bars]
    acc = [hm.get(b[0], (None,) * 5)[2] for b in bars]
    ret = [hm.get(b[0], (None,) * 5)[3] for b in bars]
    tak = [hm.get(b[0], (None,) * 5)[4] for b in bars]
    lg = lambda s: [math.log(v) if v and v > 0 else None for v in s]       # noqa: E731
    lpos, lacc, lret, ltak, loi = lg(pos), lg(acc), lg(ret), lg(tak), lg(oi)
    d_pos = [(lpos[i] - lpos[i - 4]) if i >= 4 and lpos[i] is not None and lpos[i - 4] is not None else None for i in range(n)]
    oi_chg = [(loi[i] - loi[i - 4]) if i >= 4 and loi[i] is not None and loi[i - 4] is not None else None for i in range(n)]
    z = {"tt_pos": [zscore(lpos, i) for i in range(n)], "tt_acc": [zscore(lacc, i) for i in range(n)],
         "retail": [zscore(lret, i) for i in range(n)], "taker": [zscore(ltak, i) for i in range(n)],
         "d_tt_pos": [zscore(d_pos, i) for i in range(n)], "oi_chg": [zscore(oi_chg, i) for i in range(n)]}
    z["div"] = [(a - b) if a is not None and b is not None else None for a, b in zip(z["tt_pos"], z["retail"])]
    # рост ОИ вместе с ростом / падением цены за 4ч: "новые позиции в сторону движения"
    px = [b[4] for b in bars]
    up = [(z["oi_chg"][i] if z["oi_chg"][i] is not None and i >= 4 and px[i] > px[i - 4] else None) for i in range(n)]
    dn = [(z["oi_chg"][i] if z["oi_chg"][i] is not None and i >= 4 and px[i] < px[i - 4] else None) for i in range(n)]
    z["oi_price_up"], z["oi_price_dn"] = up, dn
    return z


def collect(data: dict, feats: dict) -> tuple:
    """Сделки по всем конфигурациям и безусловные средние. -> (trades, base)
    trades[(признак, порог, 'follow'|'fade', h)] = [(время_мс, монета, доходность_%_после_комиссии, excess_%)]"""
    base = {}                                                            # (монета, h) -> среднее сырое (без комиссии), %
    for s, bars in data.items():
        for h in HORIZONS:
            rs = [(bars[i + h][4] / bars[i][4] - 1) * 100 for i in range(len(bars) - h)]
            base[(s, h)] = statistics.mean(rs) if rs else 0.0
    trades = {}
    for s, bars in data.items():
        z = feats[s]
        for f in FEATURES:
            for thr in THRESHOLDS:
                for h in HORIZONS:
                    last = -10 ** 9
                    for i in range(Z_WINDOW, len(bars) - h):
                        v = z[f][i]
                        if v is None or abs(v) < thr or i - last < h:
                            continue
                        last = i
                        raw = (bars[i + h][4] / bars[i][4] - 1) * 100
                        for mode, sd in (("follow", 1 if v > 0 else -1), ("fade", -1 if v > 0 else 1)):
                            trades.setdefault((f, thr, mode, h), []).append(
                                (bars[i][0], s, sd * raw - COST, sd * (raw - base[(s, h)])))
    return trades, base


def cluster_t(trades: list) -> tuple:
    """Среднее excess и t по дням (среднее внутри дня, затем t по дням). -> (дней, mean_excess, t)"""
    byday = {}
    for t, _s, _r, ex in trades:
        byday.setdefault(t // 86400_000, []).append(ex)
    xs = [statistics.mean(v) for v in byday.values()]
    if len(xs) < 20:
        return len(xs), 0.0, 0.0
    sd = statistics.pstdev(xs) or 1e-9
    return len(xs), statistics.mean(xs), statistics.mean(xs) / (sd / math.sqrt(len(xs)))


def evaluate(trades: dict) -> list:
    rows = []
    for key, lst in trades.items():
        if len(lst) < 200:
            continue
        lst = sorted(lst)
        half = len(lst) // 2
        d1, m1, t1 = cluster_t(lst[:half])
        d2, m2, t2 = cluster_t(lst[half:])
        da, ma, ta = cluster_t(lst)
        net = statistics.mean(r for _t, _s, r, _e in lst)
        rows.append({"key": key, "n": len(lst), "days": da, "excess": ma, "t": ta, "net": net, "m1": m1, "m2": m2})
    rows.sort(key=lambda r: min(r["m1"], r["m2"]) if r["t"] > 0 else -abs(r["t"]), reverse=True)
    return rows


def report(rows: list, data: dict, base: dict, days: int) -> str:
    good = [r for r in rows if r["m1"] > 0 and r["m2"] > 0 and r["t"] >= 3.5 and r["n"] >= 200 and r["net"] > 0]
    out = ["# Проверка идеи «крупные игроки знают больше»\n",
           f"Монет с данными: {len(data)}, история до {days} дней, конфигураций с n>=200: {len(rows)}. Комиссия {COST}% за круг.",
           "Признаки: позиции топ-трейдеров (tt_pos), счета топ-трейдеров (tt_acc), все счета (retail), расхождение топ/толпа (div),",
           "агрессивные покупки/продажи (taker), изменение tt_pos за 4ч (d_tt_pos), изменение открытого интереса (oi_chg, с ростом/падением цены).",
           "follow = в сторону отклонения признака, fade = против. excess = доходность минус безусловное среднее монеты; t - по дням.\n",
           "| признак | порог z | режим | часов | n | дней | excess % | net % после комиссии | t (по дням) | 1-я пол. excess | 2-я пол. excess |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows[:30]:
        f, thr, mode, h = r["key"]
        out.append(f"| {f} | {thr} | {mode} | {h} | {r['n']} | {r['days']} | {r['excess']:.3f} | {r['net']:.3f} | {r['t']:.2f} | {r['m1']:.3f} | {r['m2']:.3f} |")
    out.append(f"\nКонфигураций, прошедших жёсткий отбор (t>=3.5 по дням, обе половины в плюсе, n>=200, net>0): **{len(good)}** из {len(rows)}.")
    if good:
        out.append("Прошли: " + "; ".join(f"{r['key']} (t {r['t']:.2f})" for r in good[:8]))
    out.append("\nПри ~200 проверенных конфигурациях несколько случайных значимостей ожидаемы; отбор выше строже обычного, но всё равно это гипотеза "
               "для живого наблюдения.")
    return "\n".join(out)


def load_symbol(symbol: str, days: int, bars: list) -> dict:
    start = datetime.fromtimestamp(bars[0][0] / 1000, timezone.utc).date()
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    day_list = [(start + timedelta(days=k)).isoformat() for k in range((end - start).days + 1)]
    hm = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for rows in ex.map(lambda d: fetch_metrics_day(symbol, d), day_list):
            hm.update(hourly_metrics(rows))
    return hm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--out", default="smart_money_report.md")
    a = ap.parse_args()
    uni = [s for s, _ in scanner._fetch_universe()[:a.top + 10] if s not in ("USDCUSDT", "FDUSDUSDT", "TUSDUSDT")][:a.top]
    data, feats = {}, {}
    for s in uni:
        bars = bi.fetch(s, a.days)
        if len(bars) < 1000:
            continue
        hm = load_symbol(s, a.days, bars)
        cover = sum(1 for b in bars if b[0] in hm) / len(bars)
        print(f"{s}: баров {len(bars)}, покрытие метрик {cover:.0%}", file=sys.stderr)
        if cover < 0.5:
            continue
        data[s], feats[s] = bars, build_features(bars, hm)
    trades, base = collect(data, feats)
    text = report(evaluate(trades), data, base, a.days)
    open(a.out, "w", encoding="utf-8").write(text)
    print(text)


if __name__ == "__main__":
    main()
