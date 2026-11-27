#!/usr/bin/env python3
"""Проверка идей из ролика "100 000 стратегий": вход по закрытию свечи за уровнем, стоп за экстремум, цель 1:1,
таймфрейм 4ч против 1ч, фильтр по торговой сессии 13:30-21:00 UTC. Автор ролика не показал проверки вне выборки,
поэтому проверяем сами и публикуем результат в любом случае.

Стратегии (каждая - одна фиксированная конфигурация, ничего не подгоняется):
- breakout: закрытие свечи за последним подтверждённым свинг-максимумом (лонг) / свинг-минимумом (шорт), вход по закрытию;
  стоп - за противоположным свингом, цель = риск (1:1).
- retest_close: после такого пробоя ждём возврат к пробитому уровню (до 12 свечей); если свеча коснулась уровня и ЗАКРЫЛАСЬ
  за ним - вход по закрытию, стоп за экстремум этой свечи, цель 1:1 (вывод ролика: "вход после закрытия").
- retest_touch: то же, но лимитный вход в момент касания уровня (+0.2%), стоп 0.5 ATR за уровнем, цель 1:1.
Защита от самообмана: свинги подтверждаются только через k свечей (без заглядывания вперёд); вход по закрытию сигнальной
свечи, проверка стопа/цели со следующей (если в одной свече и стоп, и цель - считаем, что сработал стоп); комиссия+
проскальзывание 0.15% за круг; одна позиция на монету; значимость - t по дням (монеты в один день двигаются вместе),
порог t >= 3.5 (12 конфигураций), обе половины периода в плюсе, n >= 200. Список монет - сегодняшний топ (смещение
выжившего), фандинг не учтён."""
import argparse
import statistics
import sys
from datetime import datetime, timezone

import backtest_ideas as bi
import scanner
import smart_money_research as smr

COST = 0.15
K = 3                     # свинг подтверждается K свечами справа
RETEST_WINDOW = 12
MAX_HOLD = 48
RISK_ATR = (0.5, 4.0)     # допустимый риск в ATR (отсекаем слишком узкие и слишком широкие стопы)
H = 3600_000
STRATS = ("breakout", "retest_close", "retest_touch")
SESSION = (13, 21)        # час закрытия сигнальной свечи, UTC


def aggregate(bars: list, hours: int) -> list:
    """1ч свечи -> свечи по `hours` часов (только полные, выровнены по UTC)."""
    if hours == 1:
        return bars
    span, out, cur = hours * H, [], None
    for b in bars:
        key = b[0] // span * span
        if cur is None or cur[0] != key:
            if cur is not None and cur[6] == hours:
                out.append(cur[:6])
            cur = [key, b[1], b[2], b[3], b[4], b[5], 1]
        else:
            cur[2], cur[3], cur[4], cur[5], cur[6] = max(cur[2], b[2]), min(cur[3], b[3]), b[4], cur[5] + b[5], cur[6] + 1
    if cur is not None and cur[6] == hours:
        out.append(cur[:6])
    return [tuple(x) for x in out]


def atr_series(bars: list, n: int = 14) -> list:
    tr = [bars[0][2] - bars[0][3]] + [max(bars[i][2] - bars[i][3], abs(bars[i][2] - bars[i - 1][4]), abs(bars[i][3] - bars[i - 1][4])) for i in range(1, len(bars))]
    out = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        out[i] = sum(tr[i - n + 1:i + 1]) / n
    return out


def swings(bars: list, k: int = K) -> tuple:
    """(свинг-максимумы, свинг-минимумы): {индекс_подтверждения: (индекс_свечи, цена)}; известны только с индекса i+k."""
    hi, lo = {}, {}
    for i in range(k, len(bars) - k):
        win = bars[i - k:i + k + 1]
        if bars[i][2] >= max(b[2] for b in win):
            hi[i + k] = (i, bars[i][2])
        if bars[i][3] <= min(b[3] for b in win):
            lo[i + k] = (i, bars[i][3])
    return hi, lo


def simulate(bars: list, entry_idx: int, entry: float, stop: float, side: int) -> tuple:
    """Ведёт позицию со свечи entry_idx+1. -> (индекс выхода, цена выхода). Стоп приоритетнее цели в одной свече."""
    risk = abs(entry - stop)
    target = entry + side * risk
    last = min(len(bars) - 1, entry_idx + MAX_HOLD)
    for j in range(entry_idx + 1, last + 1):
        lo, hi = bars[j][3], bars[j][2]
        if (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
            return j, stop
        if (side == 1 and hi >= target) or (side == -1 and lo <= target):
            return j, target
    return last, bars[last][4]


def trades_for(bars: list, strat: str, session: bool) -> list:
    """Сделки одной монеты: [(время_входа_мс, чистая_доходность_%, R_чистое)]."""
    if len(bars) < 80:
        return []
    atr = atr_series(bars)
    hi_conf, lo_conf = swings(bars)
    sh = sl = None                       # последние подтверждённые свинг-максимум / минимум (цена)
    pending = []                         # пробои, ждущие возврата: [сторона, уровень, индекс_пробоя]
    out, busy_until = [], -1
    span = (bars[1][0] - bars[0][0]) if len(bars) > 1 else H

    def ok_hour(j: int) -> bool:
        if not session:
            return True
        h = datetime.fromtimestamp((bars[j][0] + span) / 1000, timezone.utc).hour
        return SESSION[0] <= h <= SESSION[1]

    def take(j: int, entry: float, stop: float, side: int, stopped_now: bool = False) -> None:
        nonlocal busy_until
        a = atr[j]
        risk = abs(entry - stop)
        if not a or risk <= 0 or not (RISK_ATR[0] * a <= risk <= RISK_ATR[1] * a) or not ok_hour(j):
            return
        xi, xp = (j, stop) if stopped_now else simulate(bars, j, entry, stop, side)
        gross = side * (xp / entry - 1) * 100
        net = gross - COST
        out.append((bars[j][0], net, net / (risk / entry * 100)))
        busy_until = xi

    for j in range(1, len(bars)):
        if j in hi_conf:
            sh = hi_conf[j][1]
        if j in lo_conf:
            sl = lo_conf[j][1]
        if j <= busy_until:
            continue
        c, prev_c = bars[j][4], bars[j - 1][4]
        if sh is not None and sl is not None:
            if c > sh >= prev_c and sl < c:
                if strat == "breakout":
                    take(j, c, sl, 1)
                else:
                    pending.append([1, sh, j])
            elif c < sl <= prev_c and sh > c:
                if strat == "breakout":
                    take(j, c, sh, -1)
                else:
                    pending.append([-1, sl, j])
        if strat == "breakout":
            continue
        keep = []
        for side, lvl, j0 in pending:
            if j == j0:                                   # в свече пробоя возврата ещё нет
                keep.append([side, lvl, j0])
                continue
            if j - j0 > RETEST_WINDOW:                    # окно ожидания истекло
                continue
            if j <= busy_until:                           # занято другой сделкой по монете: ждём дальше
                keep.append([side, lvl, j0])
                continue
            lo, hi, cl = bars[j][3], bars[j][2], bars[j][4]
            if strat == "retest_close" and ((side == 1 and cl < lvl * 0.998) or (side == -1 and cl > lvl * 1.002)):
                continue                                  # уровень не удержан: пробой ложный (решается по закрытию - для лимитного входа так нельзя)
            touched = lo <= lvl * 1.002 if side == 1 else hi >= lvl * 0.998
            if not touched:
                keep.append([side, lvl, j0])
                continue
            if strat == "retest_close":
                if (side == 1 and cl > lvl) or (side == -1 and cl < lvl):
                    take(j, cl, lo * 0.9995 if side == 1 else hi * 1.0005, side)
            else:
                a = atr[j]
                if a:
                    entry = lvl * (1.002 if side == 1 else 0.998)
                    stop = lvl - 0.5 * a if side == 1 else lvl + 0.5 * a
                    # лимит исполнен при касании; если в этой же свече выбит и стоп - это убыток (иначе была бы утечка из будущего)
                    take(j, entry, stop, side, stopped_now=(side == 1 and lo <= stop) or (side == -1 and hi >= stop))
        pending = keep
    return out


def evaluate(trades: list) -> dict:
    """trades: [(t, coin, net%, R)] -> статистика с t по дням и половинами периода."""
    if not trades:
        return {"n": 0}
    trades = sorted(trades)
    half = len(trades) // 2
    da, ma, ta = smr.cluster_t([(t, s, n, n) for t, s, n, _r in trades])
    _d1, m1, _t1 = smr.cluster_t([(t, s, n, n) for t, s, n, _r in trades[:half]])
    _d2, m2, _t2 = smr.cluster_t([(t, s, n, n) for t, s, n, _r in trades[half:]])
    coins = {}
    for _t, s, n, _r in trades:
        coins.setdefault(s, []).append(n)
    return {"n": len(trades), "days": da, "net": statistics.mean(n for _t, _s, n, _r in trades), "r": statistics.mean(r for *_x, r in trades),
            "win": sum(1 for _t, _s, n, _r in trades if n > 0) / len(trades), "t": ta, "m1": m1, "m2": m2,
            "coins_pos": sum(1 for v in coins.values() if statistics.mean(v) > 0) / len(coins)}


def verdict(r: dict) -> bool:
    return r.get("n", 0) >= 200 and r["net"] > 0 and r["m1"] > 0 and r["m2"] > 0 and r["t"] >= 3.5


def report(rows: list, ncoins: int, days: int) -> str:
    out = ["# Проверка идей из ролика: уровни, вход по закрытию, 1:1, 4ч, сессия\n",
           f"Монет: {ncoins}, история до {days} дней, комиссия+проскальзывание {COST}% за круг, цель 1:1. "
           "net - средняя чистая доходность сделки, R - в долях риска, win - доля плюсовых, t - по дням, "
           "1/2 - средняя net в первой и второй половине периода, монет+ - доля монет со средним > 0.\n",
           "| стратегия | таймфрейм | сессия 13-21 UTC | n | win | net % | R | t | 1-я пол. | 2-я пол. | монет+ | прошла |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, r in rows:
        strat, tf, sess = key
        if r.get("n", 0) == 0:
            out.append(f"| {strat} | {tf}ч | {'да' if sess else 'нет'} | 0 | | | | | | | | |")
            continue
        out.append(f"| {strat} | {tf}ч | {'да' if sess else 'нет'} | {r['n']} | {r['win']:.0%} | {r['net']:.3f} | {r['r']:.3f} | {r['t']:.2f} | "
                   f"{r['m1']:.3f} | {r['m2']:.3f} | {r['coins_pos']:.0%} | {'ДА' if verdict(r) else 'нет'} |")
    good = [k for k, r in rows if verdict(r)]
    out.append(f"\nПрошли жёсткий отбор (n>=200, net>0, обе половины в плюсе, t>=3.5): **{len(good)}** из {len(rows)}.")
    out.append("Ограничения: сегодняшний список монет (смещение выжившего), фандинг не учтён, исполнение упрощено (стоп приоритетнее цели). "
               "Результат - гипотеза для демо-проверки, не торговое правило.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=900)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out", default="levels_report.md")
    a = ap.parse_args()
    uni = [s for s, _ in scanner._fetch_universe()[:a.top + 10] if s not in ("USDCUSDT", "FDUSDUSDT", "TUSDUSDT")][:a.top]
    data = {}
    for s in uni:
        bars = bi.fetch(s, a.days)
        if len(bars) >= 2000:
            data[s] = bars
            print(f"{s}: {len(bars)} свечей", file=sys.stderr)
    rows = []
    for strat in STRATS:
        for tf in (1, 4):
            for sess in (False, True):
                tr = []
                for s, bars in data.items():
                    tr += [(t, s, n, r) for t, n, r in trades_for(aggregate(bars, tf), strat, sess)]
                rows.append(((strat, tf, sess), evaluate(tr)))
    text = report(rows, len(data), a.days)
    open(a.out, "w", encoding="utf-8").write(text)
    print(text)


if __name__ == "__main__":
    main()
