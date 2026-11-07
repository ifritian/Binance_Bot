#!/usr/bin/env python3
"""Перебор параметров идей вход/стоп/цель: есть ли вообще конфигурация, которая плюс в обеих половинах истории?
Сигналы: momentum (|24ч| >= thr, сторона по движению), fade_move (против движения), fade_rsi (RSI(14,1ч) >= 80 / <= 20, против).
Выход: стоп k*ATR(14,1ч) (>=1%), цель rr*стоп, удержание hold часов, комиссия 0.15%. Сортировка по худшей из двух половин.
ВНИМАНИЕ: перебор параметров на одной истории - это подгонка; конфигурация считается пригодной только если плюс в обеих
половинах, n >= 300 и t > 2; даже тогда это гипотеза для живой проверки, не гарантия."""
import argparse
import itertools
import math
import statistics

import backtest_ideas as bi
import backtest_movers as bm
import scanner
from compare_strategies import md_table


def sim(bars, i, side, k, rr, hold):
    entry = bars[i][4]
    a = bm.atr(bars, i)
    dist = max(k * a, entry * 0.01)
    stop, tgt = entry - side * dist, entry + side * rr * dist
    for b in bars[i + 1:i + 1 + hold]:
        if (b[3] <= stop) if side > 0 else (b[2] >= stop):
            return -dist / entry * 100 - bm.COST
        if (b[2] >= tgt) if side > 0 else (b[3] <= tgt):
            return rr * dist / entry * 100 - bm.COST
    last = bars[min(i + hold, len(bars) - 1)][4]
    return side * (last / entry - 1) * 100 - bm.COST


def signals(data):
    sig = {}
    for s, bars in data.items():
        if len(bars) < 600:
            continue
        closes = [b[4] for b in bars]
        rs = bi.rsi_series(closes)
        last = {}
        for i in range(60, len(bars) - 80):
            ch = closes[i] / closes[i - 24] - 1
            for thr in (6, 10, 15):
                if abs(ch) * 100 >= thr and i - last.get(("m", thr), -999) >= 24:
                    last[("m", thr)] = i
                    sd = 1 if ch > 0 else -1
                    sig.setdefault(f"momentum thr{thr}", []).append((s, i, sd))
                    sig.setdefault(f"fade_move thr{thr}", []).append((s, i, -sd))
            if rs[i] is not None and (rs[i] >= 80 or rs[i] <= 20) and i - last.get("r", -999) >= 24:
                last["r"] = i
                sig.setdefault("fade_rsi 80/20", []).append((s, i, -1 if rs[i] >= 80 else 1))
    return sig


def stat(xs):
    n = len(xs)
    if n < 30:
        return None
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs) or 1e-9
    return n, m, m / (sd / math.sqrt(n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--out", default="sweep_report.md")
    a = ap.parse_args()
    uni = [s for s, _ in scanner._fetch_universe()[:a.top + 10] if s not in ("USDCUSDT", "FDUSDUSDT", "TUSDUSDT")][:a.top]
    data = {s: bi.fetch(s, a.days) for s in uni}
    data = {s: b for s, b in data.items() if len(b) > 600}
    sig = signals(data)
    rows = []
    for name, lst in sig.items():
        lst = sorted(lst, key=lambda x: data[x[0]][x[1]][0])
        for k, rr, hold in itertools.product((1.0, 1.5, 2.0, 3.0), (1.0, 1.5, 2.0, 3.0), (12, 24, 48, 72)):
            res = [sim(data[s], i, sd, k, rr, hold) for s, i, sd in lst]
            h = len(res) // 2
            a1, a2, al = stat(res[:h]), stat(res[h:]), stat(res)
            if not (a1 and a2 and al):
                continue
            rows.append((min(a1[1], a2[1]), name, k, rr, hold, al, a1[1], a2[1]))
    rows.sort(reverse=True)
    out = ["# Перебор параметров идей\n", f"Монет {len(data)}, {a.days} дней. Всего конфигураций: {len(rows)}. Топ-25 по худшей из двух половин.\n",
           "| сигнал | стоп ATR | RR | часов | n | avg% | t | 1-я пол. avg% | 2-я пол. avg% |", "|---|---|---|---|---|---|---|---|---|"]
    for w, name, k, rr, hold, al, m1, m2 in rows[:25]:
        out.append(f"| {name} | {k} | {rr} | {hold} | {al[0]} | {al[1]:.3f} | {al[2]:.2f} | {m1:.3f} | {m2:.3f} |")
    pos = [r for r in rows if r[0] > 0 and r[5][2] > 2 and r[5][0] >= 300]
    out.append(f"\nКонфигураций, плюсовых в обеих половинах при n>=300 и t>2: **{len(pos)}** из {len(rows)}.")
    base = [r for r in rows if r[1] == "momentum thr6" and r[2] == 1.5 and r[3] == 2.0 and r[4] == 24]
    if base:
        out.append(f"Текущее правило бота (momentum thr6, 1.5 ATR, RR 2, 24ч): avg {base[0][5][1]:.3f}%, t {base[0][5][2]:.2f}.")
    open(a.out, "w").write("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
