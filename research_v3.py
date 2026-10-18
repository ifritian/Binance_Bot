"""
research_v3.py - третий раунд поиска стратегии (4ч, ~3 года, лонг, плавающий стоп).

Те же данные, симулятор и защита от подгонки, что в research_v2 (подбор в
3 куска + нетронутый контроль, сравнение с корзиной). Новые семейства идей:
- squeeze: сжатие волатильности (узкие полосы Боллинджера) и выход вверх;
- rs: пробой у монеты, которая сильнее BTC за 30 дней;
- volbreak: пробой на повышенном объёме;
- pullback50: откат к EMA50 в аптренде с разворотной свечой;
- breakout120(+btc) из v2 как контрольный образец для сравнения.

Строже v2: помимо "плюс в 3 кусках подбора и в контроле" считается
t-статистика результата в контроле и стабильность в обеих его половинах.
Метка РОБАСТНО ставится, только если t >= 1.5 и обе половины контроля в плюсе.
Оговорка: монеты - сегодняшний список (смещение выжившего), число проверок
велико, поэтому слабые результаты считать случайными.
"""
import argparse
import itertools
import math
import sys

import requests

import research_sweep as rs
import research_v2 as r2

WARMUP = r2.WARMUP
BARS_PER_DAY = r2.BARS_PER_DAY
RS_LOOKBACK = 30 * BARS_PER_DAY
BB_N = 20
BB_WINDOW = 180


def _bandwidth(closes: list) -> list:
    out = [None] * len(closes)
    for i in range(BB_N - 1, len(closes)):
        w = closes[i - BB_N + 1:i + 1]
        m = sum(w) / BB_N
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / BB_N)
        out[i] = 4 * sd / m if m > 0 else None
    return out


def _bw_percentile(bw: list) -> list:
    """Доля предыдущих BB_WINDOW значений ширины, которые уже этого бара (0..1)."""
    out = [None] * len(bw)
    for i in range(BB_N - 1 + BB_WINDOW, len(bw)):
        cur = bw[i]
        prev = bw[i - BB_WINDOW:i]
        if cur is None or any(p is None for p in prev):
            continue
        out[i] = sum(1 for p in prev if p < cur) / BB_WINDOW
    return out


def build_ctx(bars: list, btc_up: dict, btc_close: dict, symbol: str) -> dict:
    ctx = r2.build_ctx(bars, btc_up, None, symbol)
    closes = ctx["closes"]
    ctx["bw_pct"] = _bw_percentile(_bandwidth(closes))
    rel = [None] * len(bars)
    for i in range(RS_LOOKBACK, len(bars)):
        b_now, b_then = btc_close.get(bars[i].t), btc_close.get(bars[i - RS_LOOKBACK].t)
        if b_now and b_then:
            rel[i] = (closes[i] / closes[i - RS_LOOKBACK] - 1) - (b_now / b_then - 1)
    ctx["rel30"] = rel
    return ctx


def _base(ctx, i, n, btc):
    """Общий вход: пробой максимума n баров, выше EMA200, при btc=True BTC в аптренде."""
    if i < WARMUP + 1:
        return False
    c = ctx["closes"][i]
    if c <= rs.rolling_max(ctx["highs"], n, i) or c < ctx["ema200"][i]:
        return False
    return not btc or ctx["btc_up"].get(ctx["bars"][i].t, False)


def sig_squeeze(ctx, i, n, q):
    if not _base(ctx, i, n, True):
        return 0
    p = ctx["bw_pct"][i - 1]
    return 1 if p is not None and p <= q else 0


def sig_rs(ctx, i, n, thr):
    if not _base(ctx, i, n, True):
        return 0
    r = ctx["rel30"][i]
    return 1 if r is not None and r >= thr else 0


def sig_volbreak(ctx, i, n, ratio):
    if not _base(ctx, i, n, True):
        return 0
    avg = sum(ctx["vols"][i - 30:i]) / 30
    return 1 if avg > 0 and ctx["vols"][i] >= ratio * avg else 0


def sig_pullback50(ctx, i):
    if i < WARMUP + 1 or not ctx["btc_up"].get(ctx["bars"][i].t, False):
        return 0
    e50, e200 = ctx["ema50"][i], ctx["ema200"][i]
    c = ctx["closes"][i]
    touched = ctx["lows"][i] <= e50 * 1.01
    return 1 if (e50 > e200 and c > e50 and touched and c > ctx["highs"][i - 1]) else 0


def make_strategies() -> dict:
    s = {
        "ref_breakout120+btc": lambda c, i: 1 if _base(c, i, 120, True) else 0,
        "pullback50+btc": sig_pullback50,
    }
    for n, q in itertools.product((40, 80), (0.25,)):
        s[f"squeeze_n{n}_q{int(q * 100)}"] = (lambda c, i, n=n, q=q: sig_squeeze(c, i, n, q))
    for thr in (0.05, 0.15):
        s[f"rs_n60_thr{int(thr * 100)}"] = (lambda c, i, thr=thr: sig_rs(c, i, 60, thr))
    for ratio in (1.5, 2.5):
        s[f"volbreak_n60_x{ratio}"] = (lambda c, i, ratio=ratio: sig_volbreak(c, i, 60, ratio))
    s["rs120_thr5"] = lambda c, i: sig_rs(c, i, 120, 0.05)
    return s


def _t_stat(xs: list) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m / math.sqrt(var / n) if var > 0 else 0.0


def evaluate(contexts, data, strategy, trail_k, max_hold, t_split, t_min, t_max) -> dict:
    base = r2.evaluate(contexts, data, strategy, trail_k, max_hold, t_split, t_min)
    trades = [x for x in r2.collect_trades(contexts, data, strategy, trail_k, max_hold) if x[0] >= t_split]
    mid = t_split + (t_max - t_split) / 2
    halves = [[x[1] for x in trades if x[0] < mid], [x[1] for x in trades if x[0] >= mid]]
    base["t_net"] = _t_stat([x[1] for x in trades])
    base["half_avgs"] = [(sum(h) / len(h) if h else 0.0) for h in halves]
    base["half_n"] = [len(h) for h in halves]
    base["robust"] = bool(base["found"] and base["t_net"] >= 1.5 and all(a > 0 for a in base["half_avgs"]))
    return base


def run(data: dict):
    btc_up = r2.build_btc_regime(data["BTC"])
    btc_close = {b.t: b.c for b in data["BTC"]}
    contexts = {s: build_ctx(b, btc_up, btc_close, s) for s, b in data.items()}
    t_min = min(b.t for bars in data.values() for b in bars[WARMUP:])
    t_max = max(b.t for bars in data.values() for b in bars)
    t_split = t_min + (t_max - t_min) * r2.TRAIN_SHARE
    results = []
    for (name, strat), (tk, mh) in itertools.product(make_strategies().items(), r2.EXIT_GRID):
        res = evaluate(contexts, data, strat, tk, mh, t_split, t_min, t_max)
        res.update({"strategy": name, "trail_k": tk, "max_hold": mh})
        results.append(res)
    return results, t_min, t_split, t_max


def render(results, symbols, t_min, t_split, t_max) -> str:
    found = [r for r in results if r["found"]]
    robust = [r for r in results if r["robust"]]
    d = r2._date
    lines = [
        "# Research v3", "",
        f"Монеты ({len(symbols)}): {', '.join(symbols)}. 4ч свечи, лонг, плавающий стоп по ATR.",
        f"Подбор: {d(t_min)} .. {d(t_split)} (3 куска). Контроль: {d(t_split)} .. {d(t_max)}.",
        f"Издержки {r2.COST_PCT}% на круг. Проверено конфигураций: {len(results)}. "
        "Смещение выжившего (монеты - сегодняшний список) завышает результат.", "",
        f"## РОБАСТНО (найдено + t >= 1.5 + обе половины контроля в плюсе): {len(robust)}",
        f"## Найдено по критериям v2: {len(found)}", "",
        "| стратегия | стоп/удерж. | сделок | подбор avg% (n) по кускам | контроль n | avg% | над корзиной% | win% | t | половины контроля avg% (n) | вердикт |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: x["hold"]["avg"], reverse=True):
        s = " / ".join(f"{x['avg']:+.2f} ({x['n']})" for x in r["segs"])
        h = r["hold"]
        hv = " / ".join(f"{a:+.2f} ({n})" for a, n in zip(r["half_avgs"], r["half_n"]))
        v = "РОБАСТНО" if r["robust"] else ("найдено" if r["found"] else ("подбор ок" if r["train_ok"] else "нет"))
        lines.append(f"| {r['strategy']} | {r['trail_k']}/{r['max_hold']} | {r['total_n']} | {s} | {h['n']} | "
                     f"{h['avg']:+.2f} | {h['ex']:+.2f} | {h['win']:.0f} | {r['t_net']:.1f} | {hv} | {v} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=1095)
    p.add_argument("--symbols", default="")
    p.add_argument("--out", default="research_v3_report.md")
    args = p.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or rs.DEFAULT_SYMBOLS
    data = {}
    for s in symbols:
        try:
            bars = r2.fetch_4h(s, args.days)
        except requests.RequestException as e:
            print(f"{s}: не удалось загрузить ({e})", file=sys.stderr)
            continue
        if len(bars) > WARMUP + 300:
            data[s] = bars
    if "BTC" not in data or len(data) < 5:
        print("Недостаточно данных", file=sys.stderr)
        sys.exit(1)
    results, t_min, t_split, t_max = run(data)
    report = render(results, list(data), t_min, t_split, t_max)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
