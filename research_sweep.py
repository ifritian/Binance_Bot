"""
research_sweep.py - честная проверка: есть ли у какой-нибудь стратегии
реальное преимущество (edge) на истории Binance, С УЧЁТОМ комиссий.

Зачем: по 1000 закрытых живых сигналов бота выигрыш 28% и средний итог
около -0.06% на сделку (комиссии не считались) - это уровень случайного
входа. Прежде чем публиковать сигналы как "прибыльные", нужно найти
конфигурацию, которая работает и ВНЕ той выборки, на которой её
подбирали. Этот скрипт:

- грузит часовые свечи (по умолчанию 240 дней) для списка монет;
- прогоняет набор простых стратегий и сетку стопов/целей на ATR;
- вход строго на ОТКРЫТИИ следующей свечи после сигнала (без заглядывания
  в будущее); если за одну свечу задеты и стоп и цель - считаем стоп
  (консервативно); комиссия + проскальзывание вычитаются из каждой сделки;
- делит историю на 3 последовательных куска и показывает результат в
  каждом. "Edge" засчитывается, только если чистое матожидание положительно
  во ВСЕХ трёх кусках и сделок достаточно - это защита от подгонки под
  один период (при сотнях проверенных конфигураций что-то всегда
  выглядит хорошо случайно).

Запуск (нужен доступ к data-api.binance.vision - т.е. GitHub Actions,
см. .github/workflows/research.yml):
    python research_sweep.py --days 240 --out research_report.md
"""
import argparse
import itertools
import math
import sys
import time
from dataclasses import dataclass

import requests

BASE_URL = "https://data-api.binance.vision/api/v3"

DEFAULT_SYMBOLS = [
    "BTC", "ETH", "BNB", "XRP", "SOL", "DOGE", "ADA", "LINK", "AVAX", "SUI",
    "LTC", "DOT", "NEAR", "APT", "UNI", "AAVE", "ATOM", "ARB", "OP", "INJ",
    "TIA", "FET", "RENDER", "PEPE", "TRX",
]

ROUND_TRIP_COST_PCT = 0.20  # комиссия 0.1% с каждой стороны + проскальзывание (консервативно)
MAX_HOLD_BARS = 48
MIN_TRADES_PER_SEGMENT = 30


@dataclass
class Bar:
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float


def fetch_hourly(symbol: str, days: int) -> list[Bar]:
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    bars: list[Bar] = []
    cursor = start
    while cursor < end:
        resp = requests.get(
            f"{BASE_URL}/klines",
            params={"symbol": f"{symbol}USDT", "interval": "1h", "startTime": cursor, "limit": 1000},
            timeout=20,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        for r in rows:
            bars.append(Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])))
        cursor = int(rows[-1][0]) + 3600 * 1000
        if len(rows) < 1000:
            break
        time.sleep(0.15)
    return bars


# ---------------- индикаторы (простые списки, без numpy) ----------------

def ema(values: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    out, prev = [], values[0]
    for v in values:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(closes: list[float], period: int = 14) -> list[float]:
    out = [50.0] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / period, losses / period
    for i in range(period, len(closes)):
        if i > period:
            d = closes[i] - closes[i - 1]
            avg_g = (avg_g * (period - 1) + max(d, 0)) / period
            avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def atr(bars: list[Bar], period: int = 14) -> list[float]:
    trs = [bars[0].h - bars[0].l]
    for i in range(1, len(bars)):
        pc = bars[i - 1].c
        trs.append(max(bars[i].h - bars[i].l, abs(bars[i].h - pc), abs(bars[i].l - pc)))
    out, prev = [], trs[0]
    for i, tr in enumerate(trs):
        prev = tr if i == 0 else (prev * (period - 1) + tr) / period
        out.append(prev)
    return out


def rolling_max(values: list[float], n: int, i: int) -> float:
    return max(values[max(0, i - n):i])


def rolling_min(values: list[float], n: int, i: int) -> float:
    return min(values[max(0, i - n):i])


# ---------------- стратегии: возвращают +1 / -1 / 0 для бара i ----------------

def build_context(bars: list[Bar], btc_trend_up: dict[int, bool] | None) -> dict:
    closes = [b.c for b in bars]
    return {
        "bars": bars,
        "closes": closes,
        "highs": [b.h for b in bars],
        "lows": [b.l for b in bars],
        "vols": [b.v for b in bars],
        "ema50": ema(closes, 50),
        "ema200": ema(closes, 200),
        "rsi": rsi(closes, 14),
        "atr": atr(bars, 14),
        "btc_up": btc_trend_up,
    }


def sig_donchian(ctx, i, n=48, vol_ratio=1.5, trend=False, btc=False):
    if i < 210:
        return 0
    c = ctx["closes"][i]
    avg_vol = sum(ctx["vols"][i - 24:i]) / 24
    if avg_vol <= 0 or ctx["vols"][i] < vol_ratio * avg_vol:
        return 0
    d = 0
    if c > rolling_max(ctx["highs"], n, i):
        d = 1
    elif c < rolling_min(ctx["lows"], n, i):
        d = -1
    if d == 0:
        return 0
    if trend and ((d == 1 and c < ctx["ema200"][i]) or (d == -1 and c > ctx["ema200"][i])):
        return 0
    if btc and ctx["btc_up"] is not None and d == 1 and not ctx["btc_up"].get(ctx["bars"][i].t, True):
        return 0
    return d


def sig_trend_pullback(ctx, i, rsi_buy=40, btc=False):
    if i < 210:
        return 0
    c = ctx["closes"][i]
    up = ctx["ema50"][i] > ctx["ema200"][i] and c > ctx["ema200"][i]
    if not (up and ctx["rsi"][i] < rsi_buy and ctx["rsi"][i - 1] >= rsi_buy):
        return 0
    if btc and ctx["btc_up"] is not None and not ctx["btc_up"].get(ctx["bars"][i].t, True):
        return 0
    return 1


def sig_rsi_reversion(ctx, i, lo=25, hi=75, trend=False):
    if i < 210:
        return 0
    r = ctx["rsi"][i]
    c = ctx["closes"][i]
    if r < lo and ctx["rsi"][i - 1] >= lo and (not trend or c > ctx["ema200"][i]):
        return 1
    if r > hi and ctx["rsi"][i - 1] <= hi and (not trend or c < ctx["ema200"][i]):
        return -1
    return 0


STRATEGIES = {
    "donchian48_vol1.5": lambda c, i: sig_donchian(c, i, 48, 1.5),
    "donchian48_vol1.5+trend": lambda c, i: sig_donchian(c, i, 48, 1.5, trend=True),
    "donchian48_vol1.5+trend+btc": lambda c, i: sig_donchian(c, i, 48, 1.5, trend=True, btc=True),
    "donchian24_vol2+trend": lambda c, i: sig_donchian(c, i, 24, 2.0, trend=True),
    "trend_pullback_rsi40": lambda c, i: sig_trend_pullback(c, i, 40),
    "trend_pullback_rsi40+btc": lambda c, i: sig_trend_pullback(c, i, 40, btc=True),
    "rsi_reversion_25_75": lambda c, i: sig_rsi_reversion(c, i, 25, 75),
    "rsi_reversion_25_75+trend": lambda c, i: sig_rsi_reversion(c, i, 25, 75, trend=True),
}

# (множитель ATR для стопа, множитель ATR для цели)
EXIT_GRID = [(1.5, 2.0), (1.5, 3.0), (2.0, 3.0), (2.0, 4.0), (3.0, 3.0), (3.0, 6.0)]


# ---------------- симуляция ----------------

def simulate(ctx: dict, strategy, stop_k: float, target_k: float, cost_pct: float = ROUND_TRIP_COST_PCT):
    """-> список сделок (bar_index_входа, чистый_результат_в_%). Одна позиция за раз."""
    bars, atrs = ctx["bars"], ctx["atr"]
    trades = []
    i = 0
    n = len(bars)
    while i < n - 2:
        d = strategy(ctx, i)
        if d == 0:
            i += 1
            continue
        entry_i = i + 1
        entry = bars[entry_i].o
        a = atrs[i]
        if a <= 0 or entry <= 0:
            i += 1
            continue
        stop = entry - d * stop_k * a
        target = entry + d * target_k * a
        exit_price, exit_i = None, None
        for j in range(entry_i, min(entry_i + MAX_HOLD_BARS, n)):
            b = bars[j]
            hit_stop = b.l <= stop if d == 1 else b.h >= stop
            hit_target = b.h >= target if d == 1 else b.l <= target
            if hit_stop:  # консервативно: стоп раньше цели в одной свече
                exit_price, exit_i = stop, j
                break
            if hit_target:
                exit_price, exit_i = target, j
                break
        if exit_price is None:
            exit_i = min(entry_i + MAX_HOLD_BARS, n) - 1
            exit_price = bars[exit_i].c
        gross = d * (exit_price - entry) / entry * 100
        trades.append((entry_i, gross - cost_pct))
        i = exit_i + 1
    return trades


def summarize(pcts: list[float]) -> dict:
    if not pcts:
        return {"n": 0, "win": 0.0, "avg": 0.0, "pf": 0.0}
    wins = [p for p in pcts if p > 0]
    losses = [-p for p in pcts if p <= 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else float("inf")
    return {"n": len(pcts), "win": 100 * len(wins) / len(pcts), "avg": sum(pcts) / len(pcts), "pf": pf}


def evaluate_config(contexts: dict[str, dict], strategy, stop_k: float, target_k: float, segments: int = 3) -> dict:
    """Результат по сегментам времени (общая шкала - по времени бара, не по индексу)."""
    all_trades = []  # (timestamp, pct)
    for ctx in contexts.values():
        for idx, pct in simulate(ctx, strategy, stop_k, target_k):
            all_trades.append((ctx["bars"][idx].t, pct))
    if not all_trades:
        return {"total": summarize([]), "segments": [summarize([]) for _ in range(segments)], "robust": False}
    t_min = min(t for t, _ in all_trades)
    t_max = max(t for t, _ in all_trades)
    span = (t_max - t_min) or 1
    seg_pcts: list[list[float]] = [[] for _ in range(segments)]
    for t, p in all_trades:
        k = min(int((t - t_min) / span * segments), segments - 1)
        seg_pcts[k].append(p)
    seg_stats = [summarize(s) for s in seg_pcts]
    robust = all(s["n"] >= MIN_TRADES_PER_SEGMENT and s["avg"] > 0 for s in seg_stats)
    return {"total": summarize([p for _, p in all_trades]), "segments": seg_stats, "robust": robust}


def run_sweep(contexts: dict[str, dict]) -> list[dict]:
    results = []
    for (name, strat), (sk, tk) in itertools.product(STRATEGIES.items(), EXIT_GRID):
        res = evaluate_config(contexts, strat, sk, tk)
        res.update({"strategy": name, "stop_k": sk, "target_k": tk})
        results.append(res)
    return results


def render_report(results: list[dict], symbols: list[str], days: int) -> str:
    lines = [
        "# Research sweep: есть ли реальное преимущество?",
        "",
        f"Монеты ({len(symbols)}): {', '.join(symbols)}. История: {days} дн., часовые свечи.",
        f"Издержки: {ROUND_TRIP_COST_PCT}% на круг (комиссия+проскальзывание). Вход на открытии следующей свечи, "
        "при касании и стопа и цели в одной свече считается стоп.",
        f"Проверено конфигураций: {len(results)}. История разбита на 3 последовательных куска; "
        f"конфигурация 'устойчива', только если чистое матожидание >0 в КАЖДОМ куске при >= {MIN_TRADES_PER_SEGMENT} сделок в каждом.",
        "",
    ]
    robust = [r for r in results if r["robust"]]
    lines.append(f"## Устойчивые конфигурации: {len(robust)}")
    if not robust:
        lines.append("")
        lines.append("Ни одна проверенная конфигурация не показала положительного результата во всех трёх периодах "
                     "после издержек. Публиковать сигналы как 'прибыльные' по этим стратегиям нельзя.")
    lines.append("")
    lines.append("## Все конфигурации (по среднему чистому результату, %/сделку)")
    lines.append("")
    lines.append("| стратегия | стоп/цель (ATR) | сделок | win% | avg% | PF | кусок1 avg (n) | кусок2 avg (n) | кусок3 avg (n) | устойчива |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(results, key=lambda x: x["total"]["avg"], reverse=True):
        t = r["total"]
        segs = " | ".join(f"{s['avg']:+.2f} ({s['n']})" for s in r["segments"])
        pf = "inf" if math.isinf(t["pf"]) else f"{t['pf']:.2f}"
        lines.append(f"| {r['strategy']} | {r['stop_k']}/{r['target_k']} | {t['n']} | {t['win']:.0f} | "
                     f"{t['avg']:+.3f} | {pf} | {segs} | {'ДА' if r['robust'] else 'нет'} |")
    return "\n".join(lines) + "\n"


def build_btc_trend(bars: list[Bar]) -> dict[int, bool]:
    closes = [b.c for b in bars]
    e = ema(closes, 200)
    return {b.t: closes[i] > e[i] for i, b in enumerate(bars)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=240)
    parser.add_argument("--symbols", default="", help="через запятую, без USDT; пусто - список по умолчанию")
    parser.add_argument("--out", default="research_report.md")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or DEFAULT_SYMBOLS
    data: dict[str, list[Bar]] = {}
    for s in symbols:
        try:
            bars = fetch_hourly(s, args.days)
        except requests.RequestException as e:
            print(f"{s}: не удалось загрузить ({e})", file=sys.stderr)
            continue
        if len(bars) > 400:
            data[s] = bars
    if "BTC" not in data or len(data) < 5:
        print("Недостаточно данных для исследования", file=sys.stderr)
        sys.exit(1)

    btc_up = build_btc_trend(data["BTC"])
    contexts = {s: build_context(b, btc_up) for s, b in data.items()}
    results = run_sweep(contexts)
    report = render_report(results, list(data), args.days)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
