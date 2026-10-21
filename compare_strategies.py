#!/usr/bin/env python3
"""
Честное сравнение стратегий.

Часть A - то, что реально торгует фьючерсный бот (RSI+Bollinger, MACD, Donchian
Breakout) на 15м свечах: те же функции сигналов, что в проде (backtest.py), с
комиссиями и проскальзыванием, с разбивкой по стратегиям/сторонам/месяцам и с
проверкой "а что, если убрать лучшие сделки".
Часть B - новый класс: рыночно-нейтральный кросс-секционный моментум/реверсал по
дневным свечам (лонг сильнейших, шорт слабейших альтов, ребаланс раз в неделю).

Ограничения (в отчёте тоже): список монет - сегодняшний топ по объёму (смещение
выжившего), исполнение упрощено (вход по цене сигнала, стоп/цель без частичного
тейка и трейлинга), фандинг не учтён. Результат - оценка идеи, не обещание.
"""
import argparse
import math
import statistics
import time

import requests

import backtest
import config
import scanner

COST_PCT = 0.14        # круг: комиссия 2*0.04% + проскальзывание ~0.03% на сторону
BASE = "https://data-api.binance.vision/api/v3"


def tstat(xs):
    if len(xs) < 3:
        return 0.0
    sd = statistics.pstdev(xs)
    return (statistics.mean(xs) / (sd / math.sqrt(len(xs)))) if sd > 0 else 0.0


def stats_line(xs):
    if not xs:
        return {"n": 0}
    xs_sorted = sorted(xs)
    gain = sum(x for x in xs if x > 0)
    loss = -sum(x for x in xs if x < 0)
    return {
        "n": len(xs), "win%": round(100 * sum(1 for x in xs if x > 0) / len(xs), 1),
        "avg%": round(statistics.mean(xs), 3), "median%": round(statistics.median(xs), 3),
        "sum%": round(sum(xs), 1),
        "sum_no_top2%": round(sum(xs_sorted[:-2]), 1) if len(xs) > 2 else None,
        "sum_no_top5%": round(sum(xs_sorted[:-5]), 1) if len(xs) > 5 else None,
        "pf": round(gain / loss, 2) if loss > 0 else None, "t": round(tstat(xs), 2),
    }


def md_table(rows: dict) -> str:
    if not rows:
        return "(нет данных)\n"
    cols = ["n", "win%", "avg%", "median%", "sum%", "sum_no_top2%", "sum_no_top5%", "pf", "t"]
    out = ["| группа | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for k, v in rows.items():
        out.append(f"| {k} | " + " | ".join(str(v.get(c, "")) for c in cols) + " |")
    return "\n".join(out) + "\n"


# ---------------- Часть A ----------------

def part_a(days: float, top: int, min_score: int, use_htf: bool) -> tuple[str, list]:
    universe = scanner._fetch_universe()
    symbols = [s for s, _ in universe[:top]]
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days * 86400 * 1000)
    warm = backtest.WARMUP_CANDLES * 15 * 60 * 1000
    htf_warm = 60 * 86400 * 1000
    track = int(config.OUTCOME_MAX_TRACK_HOURS * 3600 * 1000)
    res = []
    import multi_timeframe
    for s in symbols:
        c15 = backtest._fetch_klines_range(s, "15m", start_ms - warm, end_ms + track)
        if len(c15) < backtest.WARMUP_CANDLES:
            continue
        htf = {}
        if use_htf:
            for tf in multi_timeframe.HTF_TIMEFRAMES:
                rows = backtest._fetch_klines_range(s, tf, start_ms - htf_warm, end_ms)
                htf[tf] = [(r.open_time, r.close) for r in rows]
        res += backtest.backtest_symbol(s, c15, htf, start_ms, end_ms, use_htf=use_htf, min_publish_score=min_score)
    for r in res:
        r["net"] = r["pnl_pct"] - COST_PCT
    return symbols, res


def render_a(symbols, res, label) -> str:
    out = [f"### {label}\n", f"Монет: {len(symbols)}, сигналов: {len(res)}\n"]
    out.append("**Всего**\n\n" + md_table({"все": stats_line([r['net'] for r in res])}))
    for key, title in (("strategy", "По стратегиям"), ("direction", "По сторонам")):
        groups = {}
        for r in res:
            groups.setdefault(r[key], []).append(r["net"])
        out.append(f"**{title}**\n\n" + md_table({k: stats_line(v) for k, v in sorted(groups.items())}))
    buckets = {}
    for r in res:
        lo = r["score"] // 5 * 5
        buckets.setdefault(f"score {lo}-{lo + 4}", []).append(r["net"])
    out.append("**По score (предсказывает ли он исход)**\n\n" + md_table({k: stats_line(v) for k, v in sorted(buckets.items())}))
    months = {}
    for r in res:
        m = time.strftime("%Y-%m", time.gmtime(r["signal_time_ms"] / 1000))
        months.setdefault(m, []).append(r["net"])
    out.append("**По месяцам (стабильность)**\n\n" + md_table({k: stats_line(v) for k, v in sorted(months.items())}))
    res_sorted = sorted(res, key=lambda r: r["signal_time_ms"])
    half = len(res_sorted) // 2
    out.append("**Две половины периода**\n\n" + md_table({
        "первая": stats_line([r["net"] for r in res_sorted[:half]]),
        "вторая": stats_line([r["net"] for r in res_sorted[half:]])}))
    return "\n".join(out)


# ---------------- Часть B ----------------

def fetch_daily(symbol: str, days: int) -> dict:
    out, end = {}, int(time.time() * 1000)
    start = end - days * 86400 * 1000
    cur = start
    while cur < end:
        try:
            r = requests.get(f"{BASE}/klines", params={"symbol": symbol, "interval": "1d", "startTime": cur, "limit": 1000}, timeout=20)
            r.raise_for_status()
            rows = r.json()
        except requests.RequestException:
            break
        if not rows:
            break
        for k in rows:
            out[int(k[0]) // 86400000] = float(k[4])
        cur = int(rows[-1][0]) + 1
        if len(rows) < 1000:
            break
    return out


def part_b(top: int, days: int):
    universe = scanner._fetch_universe()
    symbols = [s for s, _ in universe[: top + 10] if s not in ("USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT")][:top]
    data = {s: fetch_daily(s, days) for s in symbols}
    data = {s: d for s, d in data.items() if len(d) > 200}
    return data


def xs_backtest(data: dict, lookback: int, hold: int, frac: float, mode: str, cost_pct: float = 0.15):
    """mode: 'mom' (лонг лучших, шорт худших), 'rev' (наоборот), 'long_top_vs_btc'."""
    days = sorted(set(d for s in data.values() for d in s))
    rets, dates = [], []
    for t in days[lookback::hold]:
        sc, fwd = {}, {}
        for s, d in data.items():
            if t in d and (t - lookback) in d and (t + hold) in d:
                sc[s] = d[t] / d[t - lookback] - 1
                fwd[s] = d[t + hold] / d[t] - 1
        if len(sc) < 12:
            continue
        ranked = sorted(sc, key=sc.get)
        k = max(3, int(len(ranked) * frac))
        bottom, top = ranked[:k], ranked[-k:]
        if mode == "rev":
            top, bottom = bottom, top
        long_r = statistics.mean(fwd[s] for s in top)
        short_r = statistics.mean(fwd[s] for s in bottom)
        if mode == "long_top_vs_btc":
            btc = fwd.get("BTCUSDT")
            if btc is None:
                continue
            spread = long_r - btc
        else:
            spread = (long_r - short_r) / 2 * 2  # по 1 единице на каждую ногу: доходность спреда
        rets.append(100 * (spread - cost_pct / 100 * 2))
        dates.append(t)
    return dates, rets


def render_b(data: dict) -> str:
    out = [f"Монет в выборке: {len(data)}. Период: {len(set(d for s in data.values() for d in s))} дней.\n"]
    rows, rows_halves = {}, {}
    for mode in ("mom", "rev", "long_top_vs_btc"):
        for lb in (7, 14, 28, 56):
            for hold in (7,):
                dates, rets = xs_backtest(data, lb, hold, 0.2, mode)
                if len(rets) < 20:
                    continue
                name = f"{mode} lb{lb} hold{hold}"
                rows[name] = stats_line(rets)
                h = len(rets) // 2
                a, b = rets[:h], rets[h:]
                rows_halves[name] = (round(statistics.mean(a), 3), round(tstat(a), 2), round(statistics.mean(b), 3), round(tstat(b), 2))
    out.append("**Недельный ребаланс, 1 единица на ногу, комиссия 0.15%/сторона, % за неделю**\n\n" + md_table(rows))
    out.append("**Стабильность: среднее за неделю и t-stat в первой и второй половине**\n\n| конфиг | 1-я avg | 1-я t | 2-я avg | 2-я t |\n|---|---|---|---|---|")
    for k, v in rows_halves.items():
        out.append(f"| {k} | {v[0]} | {v[1]} | {v[2]} | {v[3]} |")
    robust = [k for k, v in rows_halves.items() if v[0] > 0 and v[2] > 0 and rows[k]["t"] >= 2.0]
    out.append(f"\n**Устойчивые (обе половины > 0 и t >= 2): {robust if robust else 'нет'}**\n")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=120)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--out", default="compare_report.md")
    args = ap.parse_args()
    parts = ["# Сравнение стратегий\n", __doc__ + "\n", "## Часть A. То, что торгует бот (15м, с комиссиями)\n"]
    syms, res = part_a(args.days, args.top, 80, True)
    parts.append(render_a(syms, res, "Как в проде: порог score 80, HTF-подтверждение"))
    syms2, res2 = part_a(args.days, args.top, 0, True)
    parts.append(render_a(syms2, res2, "Без порога score (проверка, помогает ли порог)"))
    parts.append("\n## Часть B. Кросс-секционный моментум/реверсал (дневные свечи)\n")
    data = part_b(60, 1500)
    parts.append(render_b(data))
    open(args.out, "w").write("\n".join(parts))
    print("\n".join(parts))


if __name__ == "__main__":
    main()
