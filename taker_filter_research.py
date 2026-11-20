#!/usr/bin/env python3
"""Помогает ли фильтр "агрессивные покупки/продажи согласны со стороной сигнала" стратегиям демо-фьючерсов?

Берём ровно те сделки, что даёт бэктест фьючерсного бота (compare_strategies.part_a: RSI+Bollinger, MACD, Donchian, 15м,
комиссии и проскальзывание) и для каждой смотрим z-оценку taker-потока (smart_money_research.build_features) на последнем
ЗАКРЫТОМ часе до сигнала. Сравниваем: все сделки, согласные (поток по стороне сигнала), несогласные, и сильные варианты.
Фильтр, который не улучшает среднюю сделку в обеих половинах периода, не берём. Ограничения те же: сегодняшний список
монет, фандинг не учтён, исполнение упрощено."""
import argparse
import bisect
import statistics

import compare_strategies as cs
import smart_money_research as smr
from datetime import datetime, timedelta, timezone

H = 3600_000


def hourly_z(symbol: str, start_ms: int, end_ms: int) -> tuple:
    """(отсортированные часы, {час: z taker}) за период с запасом на окно z."""
    start = datetime.fromtimestamp((start_ms - 10 * 86400_000) / 1000, timezone.utc).date()
    end = datetime.fromtimestamp(end_ms / 1000, timezone.utc).date()
    days = [(start + timedelta(days=k)).isoformat() for k in range((end - start).days + 1)]
    from concurrent.futures import ThreadPoolExecutor
    hm = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for rows in ex.map(lambda d: smr.fetch_metrics_day(symbol, d), days):
            hm.update(smr.hourly_metrics(rows))
    if not hm:
        return [], {}
    t0, t1 = min(hm), max(hm)
    bars = [(t, 1.0, 1.0, 1.0, 1.0, 1.0) for t in range(t0, t1 + H, H)]
    z = smr.build_features(bars, hm)["taker"]
    return [b[0] for b in bars], {b[0]: z[i] for i, b in enumerate(bars) if z[i] is not None}


def z_before(hours: list, zmap: dict, signal_ms: int):
    """z последнего часа, который ЗАКРЫЛСЯ не позже сигнала (час [t, t+1ч) закрыт, если t+1ч <= сигнал)."""
    i = bisect.bisect_right(hours, signal_ms - H) - 1
    return zmap.get(hours[i]) if i >= 0 else None


def side_z(r: dict, z):
    """z в сторону сделки: >0 - поток по стороне сигнала."""
    if z is None:
        return None
    return z if r["direction"] == "long" else -z


def buckets(res: list) -> dict:
    out = {"все сделки (с данными потока)": [], "поток согласен (z>0)": [], "поток согласен сильно (z>=1)": [],
           "поток против (z<0)": [], "поток против сильно (z<=-1)": []}
    for r in res:
        sz = r.get("side_z")
        if sz is None:
            continue
        out["все сделки (с данными потока)"].append(r)
        (out["поток согласен (z>0)"] if sz > 0 else out["поток против (z<0)"]).append(r)
        if sz >= 1:
            out["поток согласен сильно (z>=1)"].append(r)
        if sz <= -1:
            out["поток против сильно (z<=-1)"].append(r)
    return out


def section(title: str, res: list) -> str:
    res = sorted(res, key=lambda r: r["signal_time_ms"])
    half = len(res) // 2
    b_all, b1, b2 = buckets(res), buckets(res[:half]), buckets(res[half:])
    rows = {}
    for k in b_all:
        rows[k] = cs.stats_line([r["net"] for r in b_all[k]])
        rows[k + " | 1-я половина"] = cs.stats_line([r["net"] for r in b1[k]])
        rows[k + " | 2-я половина"] = cs.stats_line([r["net"] for r in b2[k]])
    return f"### {title}\n\nСделок с данными потока: {len(b_all['все сделки (с данными потока)'])}\n\n" + cs.md_table(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=240)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out", default="taker_filter_report.md")
    a = ap.parse_args()
    out = ["# Фильтр taker-потока для сделок демо-фьючерсов\n", __doc__ + "\n"]
    for min_score, label in ((80, "Как в проде (порог score 80, HTF)"), (0, "Без порога score (больше сделок)")):
        syms, res = cs.part_a(a.days, a.top, min_score, True)
        if not res:
            continue
        lo, hi = min(r["signal_time_ms"] for r in res), max(r["signal_time_ms"] for r in res)
        cache = {}
        for r in res:
            if r["symbol"] not in cache:
                cache[r["symbol"]] = hourly_z(r["symbol"], lo, hi)
            hours, zmap = cache[r["symbol"]]
            r["side_z"] = side_z(r, z_before(hours, zmap, r["signal_time_ms"])) if hours else None
        out.append(section(label, res))
    open(a.out, "w", encoding="utf-8").write("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
