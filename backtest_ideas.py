#!/usr/bin/env python3
"""Какие фильтры реально улучшают идеи "вход/стоп/цель" (trade_idea): бэктест на истории с теми же правилами выхода
(стоп 1.5*ATR(14, 1ч), не ближе 1%, цель 2R, 24 часа, комиссия 0.15%).

Идеи "за движением" (momentum, |24ч| >= 6%) и "против движения" (fade, RSI(14,1ч) >= 80 / <= 20) - без фильтров и с каждым
фильтром отдельно и вместе. Каждая строка делится на две половины по времени: хорошая идея должна быть плюсом в обеих.
Смещение выжившего: список монет сегодняшний (топ по обороту)."""
import argparse
import time

import backtest_movers as bm
import scanner
from compare_strategies import md_table, stats_line


def fetch(symbol, days):
    out, end = [], int(time.time() * 1000)
    cur = end - days * 86400_000
    while cur < end:
        try:
            r = bm.requests.get(f"{bm.BASE}/klines", params={"symbol": symbol, "interval": "1h", "startTime": cur, "limit": 1000}, timeout=20)
            r.raise_for_status()
            rows = r.json()
        except bm.requests.RequestException:
            break
        if not rows:
            break
        out += [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in rows]
        cur = int(rows[-1][0]) + 1
        if len(rows) < 1000:
            break
    return out


def ema(closes, n=20):
    k, e, out = 2 / (n + 1), closes[0], []
    for c in closes:
        e = c * k + e * (1 - k)
        out.append(e)
    return out


def rsi_series(closes, n=14):
    out = [None] * len(closes)
    ag = al = None
    for i in range(1, len(closes)):
        g, l = max(closes[i] - closes[i - 1], 0), max(closes[i - 1] - closes[i], 0)
        if i <= n:
            ag = (ag or 0) + g / n
            al = (al or 0) + l / n
            if i == n:
                out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
            continue
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def features(bars, btc_by_t):
    closes = [b[4] for b in bars]
    return ema(closes), rsi_series(closes), btc_by_t


def run(data, btc):
    btc_ch = {}
    for i in range(24, len(btc)):
        btc_ch[btc[i][0]] = btc[i][4] / btc[i - 24][4] - 1
    res = {}

    def add(name, t, v):
        res.setdefault(name, []).append((t, v))

    for s, bars in data.items():
        if len(bars) < 600:
            continue
        closes = [b[4] for b in bars]
        e20, rs = ema(closes), rsi_series(closes)
        last_m = last_f = -10_000
        for i in range(60, len(bars) - 25):
            t = bars[i][0]
            a = bm.atr(bars, i)
            c = closes[i]
            ch = c / closes[i - 24] - 1
            v3 = sum(b[5] for b in bars[i - 2:i + 1]) / 3
            v24 = sum(b[5] for b in bars[i - 26:i - 2]) / 24 or 1e-9
            vol_ok = v3 / v24 >= 1.5
            btc24 = btc_ch.get(t, 0.0)
            # ---- momentum ----
            if abs(ch) * 100 >= 6 and i - last_m >= 24:
                last_m = i
                side = 1 if ch > 0 else -1
                r = bm.simulate(bars, i, side)
                trend_ok = (c > e20[i]) if side > 0 else (c < e20[i])
                chase_ok = (rs[i] is not None) and ((rs[i] < 75) if side > 0 else (rs[i] > 25))
                btc_ok = (btc24 > -0.02) if side > 0 else (btc24 < 0.02)
                width_ok = a * 1.5 / c <= 0.05
                add("momentum: без фильтров", t, r)
                if vol_ok: add("momentum: + всплеск объёма", t, r)
                if trend_ok: add("momentum: + выше/ниже EMA20", t, r)
                if chase_ok: add("momentum: + не гонимся (RSI<75/>25)", t, r)
                if btc_ok: add("momentum: + режим BTC", t, r)
                if width_ok: add("momentum: + стоп не шире 5%", t, r)
                if vol_ok and trend_ok and chase_ok and btc_ok and width_ok:
                    add("momentum: ВСЕ фильтры", t, r)
                if vol_ok and trend_ok and btc_ok:
                    add("momentum: объём + EMA20 + BTC", t, r)
            # ---- fade по RSI ----
            if rs[i] is not None and (rs[i] >= 80 or rs[i] <= 20) and i - last_f >= 24:
                last_f = i
                high = rs[i] >= 80
                side = -1 if high else 1
                r = bm.simulate(bars, i, side)
                rev_ok = (c < bars[i][1]) if high else (c > bars[i][1])
                ext_ok = abs(c - e20[i]) / a >= 3
                move_ok = abs(ch) * 100 >= 6
                add("fade RSI: без фильтров", t, r)
                if rev_ok: add("fade RSI: + разворотная свеча", t, r)
                if ext_ok: add("fade RSI: + отрыв от EMA20 >= 3 ATR", t, r)
                if move_ok: add("fade RSI: + движение 24ч >= 6%", t, r)
                if rev_ok and ext_ok: add("fade RSI: свеча + отрыв", t, r)
                if rev_ok and ext_ok and move_ok: add("fade RSI: ВСЕ фильтры", t, r)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--out", default="ideas_report.md")
    a = ap.parse_args()
    uni = [s for s, _ in scanner._fetch_universe()[:a.top + 10] if s not in ("USDCUSDT", "FDUSDUSDT", "TUSDUSDT")][:a.top]
    if "BTCUSDT" not in uni:
        uni.append("BTCUSDT")
    data = {s: fetch(s, a.days) for s in uni}
    btc = data.get("BTCUSDT") or []
    data = {s: b for s, b in data.items() if len(b) > 600}
    res = run(data, btc)
    out = ["# Фильтры для идей вход/стоп/цель\n",
           f"Монет: {len(data)}, глубина {a.days} дней, час-свечи, комиссия {bm.COST}% за круг, стоп 1.5 ATR (>=1%), цель 2R, 24 ч.\n"
           "Смещение выжившего: список монет сегодняшний. Читать так: идея полезна, если плюс в ОБЕИХ половинах и n не мизерное.\n"]
    rows = {}
    for name in sorted(res):
        lst = sorted(res[name])
        h = len(lst) // 2
        rows[name] = stats_line([x for _, x in lst])
        rows[name + " [1-я пол.]"] = stats_line([x for _, x in lst[:h]])
        rows[name + " [2-я пол.]"] = stats_line([x for _, x in lst[h:]])
    out.append(md_table(rows))
    open(a.out, "w").write("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
