"""
research_v2.py - поиск стратегии с реальным преимуществом, вторая попытка.

Отличия от research_sweep.py (где устойчивых конфигураций не нашлось):
- 4-часовые свечи и ~3 года истории (разные рыночные фазы, не один период);
- только лонги (спот, как у подписчиков) и ДЛИННЫЕ сделки: комиссия 0.2% на
  круг не съедает преимущество, если средняя сделка идёт дни, а не часы;
- плавающий стоп (trailing по ATR) вместо фиксированной цели: прибыль
  "бежит", убытки обрезаются;
- сравнение с корзиной: из результата каждой сделки вычитается, сколько
  заработала бы равновесная корзина монет за то же время. Иначе в растущем
  рынке любая покупка выглядит гением;
- защита от подгонки: история делится на ПОДБОР (первые 70%, три куска) и
  КОНТРОЛЬ (последние 30%, не участвует в выборе). Конфигурация считается
  найденной, только если она положительна в каждом из трёх кусков подбора
  И в контроле, и в контроле же обгоняет корзину;
- честная оговорка в отчёте: чем больше проверено вариантов, тем больше
  случайных "победителей".

Запуск: GitHub Actions, workflow "Research v2" (нужен data-api.binance.vision).
"""
import argparse
import bisect
import itertools
import sys
import time

import requests

import research_sweep as rs

INTERVAL_MS = 4 * 3600 * 1000
BARS_PER_DAY = 6
COST_PCT = rs.ROUND_TRIP_COST_PCT
TRAIN_SHARE = 0.70
MIN_TRADES_TRAIN_SEG = 20
MIN_TRADES_HOLDOUT = 20
WARMUP = 300  # баров на прогрев индикаторов (EMA 200 и т.п.)


def fetch_4h(symbol: str, days: int) -> list:
    end = int(time.time() * 1000)
    cursor = end - days * 86400 * 1000
    bars = []
    while cursor < end:
        resp = requests.get(
            f"{rs.BASE_URL}/klines",
            params={"symbol": f"{symbol}USDT", "interval": "4h", "startTime": cursor, "limit": 1000},
            timeout=20,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        for r in rows:
            bars.append(rs.Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])))
        cursor = int(rows[-1][0]) + INTERVAL_MS
        if len(rows) < 1000:
            break
        time.sleep(0.15)
    return bars


# ---------------- контекст ----------------

def build_ctx(bars: list, btc_up: dict | None, rank_sets: dict | None, symbol: str) -> dict:
    ctx = rs.build_context(bars, btc_up)
    ctx["symbol"] = symbol
    ctx["rank_sets"] = rank_sets  # {lookback: {t: set(symbols)}} или None
    return ctx


def build_btc_regime(btc_bars: list) -> dict:
    """BTC в аптренде: цена выше EMA(300 баров ~ 50 дней)."""
    closes = [b.c for b in btc_bars]
    e = rs.ema(closes, 300)
    return {b.t: closes[i] > e[i] for i, b in enumerate(btc_bars)}


def build_rank_sets(data: dict, lookback: int, top_k: int, rebalance_every: int = 42) -> dict:
    """На каждом шаге ребалансировки (раз в ~неделю) - top_k монет по
    доходности за lookback баров. Ключ - время бара."""
    ref = data["BTC"]
    index = {s: {b.t: i for i, b in enumerate(bars)} for s, bars in data.items()}
    out = {}
    for n, b in enumerate(ref):
        if n < lookback or n % rebalance_every != 0:
            continue
        scored = []
        for s, bars in data.items():
            i = index[s].get(b.t)
            if i is None or i < lookback:
                continue
            scored.append(((bars[i].c / bars[i - lookback].c) - 1, s))
        scored.sort(reverse=True)
        out[b.t] = {s for _, s in scored[:top_k]}
    return out


# ---------------- сигналы (только лонг) ----------------

def sig_breakout(ctx, i, n, regime):
    if i < WARMUP:
        return 0
    c = ctx["closes"][i]
    if c <= rs.rolling_max(ctx["highs"], n, i):
        return 0
    if regime and not ctx["btc_up"].get(ctx["bars"][i].t, False):
        return 0
    if c < ctx["ema200"][i]:
        return 0
    return 1


def sig_pullback(ctx, i, regime):
    if i < WARMUP:
        return 0
    c = ctx["closes"][i]
    up = ctx["ema50"][i] > ctx["ema200"][i] and c > ctx["ema200"][i]
    if not (up and ctx["rsi"][i] < 40 and ctx["rsi"][i - 1] >= 40):
        return 0
    if regime and not ctx["btc_up"].get(ctx["bars"][i].t, False):
        return 0
    return 1


def sig_momentum(ctx, i, key):
    if i < WARMUP:
        return 0
    t = ctx["bars"][i].t
    members = ctx["rank_sets"][key].get(t)
    if not members or ctx["symbol"] not in members:
        return 0
    if not ctx["btc_up"].get(t, False):
        return 0
    return 1


def make_strategies(rank_keys: list) -> dict:
    s = {}
    for n in (30, 60, 120):  # 5 / 10 / 20 дней
        s[f"breakout{n}"] = (lambda c, i, n=n: sig_breakout(c, i, n, False))
        s[f"breakout{n}+btc"] = (lambda c, i, n=n: sig_breakout(c, i, n, True))
    s["pullback"] = lambda c, i: sig_pullback(c, i, False)
    s["pullback+btc"] = lambda c, i: sig_pullback(c, i, True)
    for key in rank_keys:
        lb, k = key
        s[f"momentum_{lb // BARS_PER_DAY}d_top{k}"] = (lambda c, i, key=key: sig_momentum(c, i, key))
    return s


# (множитель ATR для плавающего стопа, макс. удержание в барах)
EXIT_GRID = [(2.5, 30), (2.5, 90), (3.5, 30), (3.5, 90), (5.0, 90)]


# ---------------- симуляция ----------------

def simulate_trailing(ctx: dict, strategy, trail_k: float, max_hold: int, cost_pct: float = COST_PCT):
    """Лонг: вход на открытии следующего бара; стоп = максимум закрытий с входа
    минус trail_k*ATR(на входе), подтягивается только вверх; выход по стопу
    (если low задел - по цене стопа) или по времени. Возвращает список
    (индекс_входа, индекс_выхода, чистый_%)."""
    bars, atrs = ctx["bars"], ctx["atr"]
    n = len(bars)
    trades = []
    i = 0
    while i < n - 2:
        if strategy(ctx, i) != 1:
            i += 1
            continue
        entry_i = i + 1
        entry = bars[entry_i].o
        a = atrs[i]
        if a <= 0 or entry <= 0:
            i += 1
            continue
        peak = entry
        stop = entry - trail_k * a
        exit_price, exit_i = None, None
        last = min(entry_i + max_hold, n)
        for j in range(entry_i, last):
            b = bars[j]
            if b.l <= stop:
                exit_price, exit_i = min(stop, b.o), j  # гэп вниз - по открытию
                break
            peak = max(peak, b.c)
            stop = max(stop, peak - trail_k * a)
        if exit_price is None:
            exit_i = last - 1
            exit_price = bars[exit_i].c
        trades.append((entry_i, exit_i, (exit_price - entry) / entry * 100 - cost_pct))
        i = exit_i + 1
    return trades


_BASKET_CACHE: dict = {}


def basket_return(data: dict, t0: int, t1: int) -> float:
    """Средняя доходность (%) равновесной корзины за [t0, t1] по ценам закрытия."""
    key = (id(data), t0, t1)
    if key in _BASKET_CACHE:
        return _BASKET_CACHE[key]
    rets = []
    for bars in data.values():
        times = [b.t for b in bars]
        i0 = bisect.bisect_left(times, t0)
        i1 = bisect.bisect_right(times, t1) - 1
        if i0 < len(bars) and i1 >= 0 and i1 >= i0:
            rets.append((bars[i1].c / bars[i0].c - 1) * 100)
    out = sum(rets) / len(rets) if rets else 0.0
    _BASKET_CACHE[key] = out
    return out


def collect_trades(contexts: dict, data: dict, strategy, trail_k: float, max_hold: int) -> list:
    """-> [(t_входа, чистый_%, избыток_над_корзиной_%)]"""
    out = []
    for ctx in contexts.values():
        bars = ctx["bars"]
        for ei, xi, pct in simulate_trailing(ctx, strategy, trail_k, max_hold):
            out.append((bars[ei].t, pct, pct - basket_return(data, bars[ei].t, bars[xi].t)))
    return out


def _avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def evaluate(contexts: dict, data: dict, strategy, trail_k: float, max_hold: int, t_split: int, t_min: int) -> dict:
    trades = collect_trades(contexts, data, strategy, trail_k, max_hold)
    train = [x for x in trades if x[0] < t_split]
    hold = [x for x in trades if x[0] >= t_split]
    seg_len = (t_split - t_min) / 3
    segs = [[x for x in train if min(int((x[0] - t_min) / seg_len), 2) == k] for k in range(3)]
    seg_stats = [{"n": len(s), "avg": _avg([x[1] for x in s]), "ex": _avg([x[2] for x in s])} for s in segs]
    hold_stats = {
        "n": len(hold),
        "avg": _avg([x[1] for x in hold]),
        "ex": _avg([x[2] for x in hold]),
        "win": 100 * sum(1 for x in hold if x[1] > 0) / len(hold) if hold else 0.0,
    }
    train_ok = all(s["n"] >= MIN_TRADES_TRAIN_SEG and s["avg"] > 0 for s in seg_stats)
    hold_ok = hold_stats["n"] >= MIN_TRADES_HOLDOUT and hold_stats["avg"] > 0 and hold_stats["ex"] > 0
    return {
        "total_n": len(trades), "segs": seg_stats, "hold": hold_stats,
        "train_ok": train_ok, "found": train_ok and hold_ok,
    }


def run(data: dict, rank_specs: list) -> tuple:
    btc_up = build_btc_regime(data["BTC"])
    rank_sets = {(lb, k): build_rank_sets(data, lb, k) for lb, k in rank_specs}
    contexts = {s: build_ctx(b, btc_up, rank_sets, s) for s, b in data.items()}
    t_min = min(b.t for bars in data.values() for b in bars[WARMUP:])
    t_max = max(b.t for bars in data.values() for b in bars)
    t_split = t_min + (t_max - t_min) * TRAIN_SHARE
    results = []
    for (name, strat), (tk, mh) in itertools.product(make_strategies(list(rank_sets)).items(), EXIT_GRID):
        res = evaluate(contexts, data, strat, tk, mh, t_split, t_min)
        res.update({"strategy": name, "trail_k": tk, "max_hold": mh})
        results.append(res)
    return results, t_min, t_split, t_max


def _date(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def render(results: list, symbols: list, t_min: int, t_split: int, t_max: int) -> str:
    n = len(results)
    found = [r for r in results if r["found"]]
    lines = [
        "# Research v2: поиск стратегии с преимуществом",
        "",
        f"Монеты ({len(symbols)}): {', '.join(symbols)}. 4-часовые свечи, только лонг, плавающий стоп по ATR.",
        f"Подбор: {_date(t_min)} .. {_date(t_split)} (3 куска). Контроль, не участвовал в выборе: {_date(t_split)} .. {_date(t_max)}.",
        f"Издержки {COST_PCT}% на круг. 'Над корзиной' = результат сделки минус доходность равновесной корзины монет за то же время.",
        f"Проверено конфигураций: {n}. При таком числе проверок несколько 'победителей' бывают случайными: "
        "относиться осторожно к тем, что едва проходят порог.",
        "",
        f"## Найдено (плюс в 3 кусках подбора И в контроле И над корзиной в контроле): {len(found)}",
    ]
    if not found:
        lines += ["", "Ничего не прошло оба фильтра. Это честный результат: по этим идеям преимущества нет."]
    lines += [
        "",
        "## Все конфигурации (по результату в контроле)",
        "",
        "| стратегия | стоп ATR / удержание (баров) | сделок всего | подбор: кусок1 / 2 / 3 avg% (n) | контроль: n | avg% | над корзиной% | win% | вердикт |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: x["hold"]["avg"], reverse=True):
        s = " / ".join(f"{x['avg']:+.2f} ({x['n']})" for x in r["segs"])
        h = r["hold"]
        verdict = "НАЙДЕНО" if r["found"] else ("подбор ок, контроль нет" if r["train_ok"] else "нет")
        lines.append(f"| {r['strategy']} | {r['trail_k']} / {r['max_hold']} | {r['total_n']} | {s} | "
                     f"{h['n']} | {h['avg']:+.2f} | {h['ex']:+.2f} | {h['win']:.0f} | {verdict} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=1095)
    p.add_argument("--symbols", default="")
    p.add_argument("--out", default="research_v2_report.md")
    args = p.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or rs.DEFAULT_SYMBOLS
    data = {}
    for s in symbols:
        try:
            bars = fetch_4h(s, args.days)
        except requests.RequestException as e:
            print(f"{s}: не удалось загрузить ({e})", file=sys.stderr)
            continue
        if len(bars) > WARMUP + 300:
            data[s] = bars
    if "BTC" not in data or len(data) < 5:
        print("Недостаточно данных", file=sys.stderr)
        sys.exit(1)
    rank_specs = [(30 * BARS_PER_DAY, 3), (30 * BARS_PER_DAY, 5), (14 * BARS_PER_DAY, 3), (14 * BARS_PER_DAY, 5)]
    results, t_min, t_split, t_max = run(data, rank_specs)
    report = render(results, list(data), t_min, t_split, t_max)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
