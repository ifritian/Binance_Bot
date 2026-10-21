#!/usr/bin/env python3
"""Проверка правила "сетап по движущейся монете" (mover_setups.py) на истории:
сигнал при |24ч изменении| >= порога, вход по цене закрытия часа, стоп 1.5*ATR(14, 1ч)
(не ближе 1%), цель 2R, 24 часа, комиссии 0.15%. Сравниваем направление "за движением"
(как в живом боте) и "против" (fade) с теми же стопом/целью."""
import argparse
import math
import statistics
import time

import requests

import scanner
from compare_strategies import md_table, stats_line

BASE = "https://data-api.binance.vision/api/v3"
COST = 0.15


def fetch_hourly(symbol, days):
    out, end = [], int(time.time() * 1000)
    cur = end - days * 86400_000
    while cur < end:
        try:
            r = requests.get(f"{BASE}/klines", params={"symbol": symbol, "interval": "1h", "startTime": cur, "limit": 1000}, timeout=20)
            r.raise_for_status()
            rows = r.json()
        except requests.RequestException:
            break
        if not rows:
            break
        out += [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in rows]
        cur = int(rows[-1][0]) + 1
        if len(rows) < 1000:
            break
    return out


def atr(bars, i, n=14):
    trs = [max(bars[j][2] - bars[j][3], abs(bars[j][2] - bars[j - 1][4]), abs(bars[j][3] - bars[j - 1][4])) for j in range(i - n + 1, i + 1)]
    return sum(trs) / n


def simulate(bars, i, side, stop_atr=1.5, rr=2.0, hold=24):
    entry = bars[i][4]
    a = atr(bars, i)
    dist = max(stop_atr * a, entry * 0.01)
    stop = entry - side * dist
    target = entry + side * rr * dist
    for b in bars[i + 1:i + 1 + hold]:
        hit_stop = b[3] <= stop if side > 0 else b[2] >= stop
        hit_tp = b[2] >= target if side > 0 else b[3] <= target
        if hit_stop:
            return -dist / entry * 100 - COST
        if hit_tp:
            return rr * dist / entry * 100 - COST
    last = bars[min(i + hold, len(bars) - 1)][4]
    return side * (last / entry - 1) * 100 - COST


def run(data, thr):
    mom, fade = [], []
    for s, bars in data.items():
        last_sig = -10_000
        for i in range(30, len(bars) - 25):
            if i - last_sig < 24:
                continue
            ch = bars[i][4] / bars[i - 24][4] - 1
            if abs(ch) * 100 < thr:
                continue
            last_sig = i
            side = 1 if ch > 0 else -1
            t = bars[i][0]
            mom.append((t, simulate(bars, i, side)))
            fade.append((t, simulate(bars, i, -side)))
    return mom, fade


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--out", default="movers_report.md")
    a = ap.parse_args()
    uni = [s for s, _ in scanner._fetch_universe()[:a.top + 10] if s not in ("USDCUSDT", "FDUSDUSDT", "TUSDUSDT")][:a.top]
    data = {s: fetch_hourly(s, a.days) for s in uni}
    data = {s: b for s, b in data.items() if len(b) > 500}
    out = ["# Сетапы по движущимся монетам: правило бота против обратного\n",
           f"Монет: {len(data)}, глубина {a.days} дней, час-свечи, комиссии {COST}% за круг. Смещение выжившего: список монет сегодняшний.\n"]
    for thr in (6, 10):
        mom, fade = run(data, thr)
        mom.sort(); fade.sort()
        h = len(mom) // 2
        out.append(f"## Порог движения за 24ч: {thr}%\n")
        rows = {"за движением (как в боте)": stats_line([x for _, x in mom]),
                "против движения (fade)": stats_line([x for _, x in fade]),
                "за движением, 1-я половина": stats_line([x for _, x in mom[:h]]),
                "за движением, 2-я половина": stats_line([x for _, x in mom[h:]]),
                "против, 1-я половина": stats_line([x for _, x in fade[:h]]),
                "против, 2-я половина": stats_line([x for _, x in fade[h:]])}
        out.append(md_table(rows))
    open(a.out, "w").write("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
