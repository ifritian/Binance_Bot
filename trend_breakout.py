"""
trend_breakout.py - стратегия "Пробой 20 дней" (4ч свечи, лонг, плавающий стоп).

Найдена в research_v2.py (3 года истории, 25 монет, комиссия 0.2% на круг):
плюс во всех трёх кусках подбора и в контрольном периоде, который в выборе
не участвовал (+0.4..+0.7% на сделку после издержек, ~40% сделок в плюс).
Преимущество над простой корзиной монет маленькое, поэтому бот НЕ называет
стратегию прибыльной: он ведёт живой счёт ВСЕХ закрытых сделок (и плюсовых,
и минусовых) и показывает его в каждом посте.

Правила (ровно те же, что в исследовании):
- сигнал на ЗАКРЫТОЙ 4ч свече: закрытие выше максимума предыдущих 120 свечей
  (20 дней), закрытие выше EMA(200), BTC выше своей EMA(300);
- вход по ОТКРЫТИЮ следующей свечи (ровно как в исследовании);
- стартовый стоп = вход - 3.5*ATR(14); дальше стоп подтягивается вверх за
  максимумом закрытий (peak - 3.5*ATR), вниз не двигается; фиксированной
  цели нет;
- выход: low свечи задел стоп (по цене стопа, при гэпе вниз - по открытию)
  либо прошло 30 свечей (5 суток) - по закрытию;
- чистый результат = (выход-вход)/вход - 0.2% издержек.

Модуль детерминированный, без LLM: все числа и тексты собирает код.
Состояние - queue_manager._get/_set (ключ trend_breakout_state).
"""
import logging
import time

import requests

import config
import coin_whitelist
import queue_manager
import research_sweep as rs

logger = logging.getLogger(__name__)

BAR_MS = 4 * 3600 * 1000
BREAKOUT_N = 120      # 20 дней по 6 свечей
EMA_TREND = 200
BTC_EMA = 300
TRAIL_K = 3.5
MAX_HOLD = 30         # свечей (5 суток)
COST_PCT = rs.ROUND_TRIP_COST_PCT
MIN_BARS = 330        # прогрев индикаторов
FETCH_LIMIT = 500
ENTRY_MAX_LATE_MS = 90 * 60 * 1000   # опоздали с обнаружением больше чем на 1.5ч - вход пропускаем
ENTRY_EVENT_TTL_S = 6 * 3600         # пост о входе не публикуем, если он устарел
EXIT_EVENT_TTL_S = 48 * 3600
STATE_KEY = "trend_breakout_state"
CLOSED_CAP = 500


def _state() -> dict:
    st = queue_manager._get(STATE_KEY) or {}
    st.setdefault("last_bar_t", 0)
    st.setdefault("positions", {})
    st.setdefault("closed", [])
    st.setdefault("outbox", [])
    return st


def _save(st: dict) -> None:
    st["closed"] = st["closed"][-CLOSED_CAP:]
    queue_manager._set(STATE_KEY, st)


# ---------------- данные ----------------

def fetch_bars(symbol: str, limit: int = FETCH_LIMIT) -> list:
    """4ч свечи (последняя может быть ещё не закрыта). [] при ошибке сети."""
    try:
        resp = requests.get(
            f"{rs.BASE_URL}/klines",
            params={"symbol": f"{symbol}USDT", "interval": "4h", "limit": limit},
            timeout=20,
        )
        resp.raise_for_status()
        return [rs.Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]))
                for r in resp.json()]
    except (requests.RequestException, ValueError, IndexError) as e:
        logger.warning("Пробой 20 дней: нет свечей %s: %s", symbol, e)
        return []


def split_bars(bars: list, now_ms: int):
    """-> (закрытые свечи, текущая незакрытая или None)"""
    closed = [b for b in bars if b.t + BAR_MS <= now_ms]
    live = next((b for b in bars if b.t + BAR_MS > now_ms), None)
    return closed, live


def btc_uptrend(btc_closed: list) -> bool:
    if len(btc_closed) < MIN_BARS:
        return False
    closes = [b.c for b in btc_closed]
    return closes[-1] > rs.ema(closes, BTC_EMA)[-1]


# ---------------- сигнал и сопровождение ----------------

def breakout_signal(closed: list, btc_up: bool):
    """Сигнал на последней закрытой свече -> {"atr", "close", "level"} или None."""
    if not btc_up or len(closed) < MIN_BARS:
        return None
    closes = [b.c for b in closed]
    c = closes[-1]
    prev_high = max(b.h for b in closed[-1 - BREAKOUT_N:-1])
    if c <= prev_high or c <= rs.ema(closes, EMA_TREND)[-1]:
        return None
    a = rs.atr(closed, 14)[-1]
    if a <= 0:
        return None
    return {"atr": a, "close": c, "level": prev_high}


def new_position(entry: float, atr: float, bar_t: int) -> dict:
    return {"entry": entry, "atr": atr, "peak": entry, "stop": entry - TRAIL_K * atr,
            "stop0": entry - TRAIL_K * atr, "held": 0, "last_t": bar_t, "entry_t": bar_t}


def advance_position(pos: dict, closed: list):
    """Прогоняет новые закрытые свечи через правила выхода (как simulate_trailing
    в research_v2). -> (pos, exit_dict | None). exit_dict: {"exit","reason","held"}"""
    for b in closed:
        if b.t <= pos["last_t"]:
            continue
        pos["last_t"] = b.t
        pos["held"] += 1
        if b.l <= pos["stop"]:
            return pos, {"exit": min(pos["stop"], b.o), "reason": "stop", "held": pos["held"]}
        pos["peak"] = max(pos["peak"], b.c)
        pos["stop"] = max(pos["stop"], pos["peak"] - TRAIL_K * pos["atr"])
        if pos["held"] >= MAX_HOLD:
            return pos, {"exit": b.c, "reason": "time", "held": pos["held"]}
    return pos, None


def net_pnl_pct(entry: float, exit_price: float) -> float:
    return (exit_price - entry) / entry * 100 - COST_PCT


def universe() -> list:
    return [s for s in rs.DEFAULT_SYMBOLS if coin_whitelist.is_allowed(s)]


# ---------------- цикл ----------------

def run_cycle(now_ms: int | None = None, fetch=None) -> None:
    """Один проход: раз в 4 часа (когда закрылась новая BTC-свеча) обновляет
    открытые позиции и ищет новые входы. Новые события кладёт в outbox."""
    fetch = fetch or fetch_bars
    now_ms = now_ms or int(time.time() * 1000)
    st = _state()

    btc_closed, _ = split_bars(fetch("BTC"), now_ms)
    if len(btc_closed) < MIN_BARS:
        return
    bar_t = btc_closed[-1].t
    if bar_t <= st["last_bar_t"]:
        return  # новой закрытой 4ч свечи ещё нет
    btc_up = btc_uptrend(btc_closed)
    ts = now_ms / 1000

    for sym in list(st["positions"]):
        closed, _ = split_bars(fetch(sym), now_ms)
        if not closed:
            continue
        pos, ex = advance_position(st["positions"][sym], closed)
        if ex is None:
            st["positions"][sym] = pos
            continue
        pnl = net_pnl_pct(pos["entry"], ex["exit"])
        rec = {"ticker": sym, "entry": pos["entry"], "exit": ex["exit"], "pnl": pnl,
               "held": ex["held"], "reason": ex["reason"], "ts": ts,
               "entry_t": pos["entry_t"] + BAR_MS, "stop0": pos["stop0"]}
        st["closed"].append(rec)
        st["outbox"].append({"kind": "exit", **rec})
        del st["positions"][sym]

    late = now_ms - (bar_t + BAR_MS)
    if late <= ENTRY_MAX_LATE_MS:
        for sym in universe():
            if sym in st["positions"] or len(st["positions"]) >= config.TREND_BREAKOUT_MAX_OPEN:
                continue
            closed, live = split_bars(fetch(sym), now_ms)
            if live is None or not closed or closed[-1].t != bar_t:
                continue
            sig = breakout_signal(closed, btc_up)
            if sig is None:
                continue
            pos = new_position(live.o, sig["atr"], bar_t)
            st["positions"][sym] = pos
            st["outbox"].append({
                "kind": "entry", "ticker": sym, "entry": pos["entry"], "stop": pos["stop0"],
                "risk_pct": (pos["entry"] - pos["stop0"]) / pos["entry"] * 100, "ts": ts,
                "level": sig["level"], "entry_t": bar_t + BAR_MS,
            })
    else:
        logger.info("Пробой 20 дней: свеча закрылась %.0f мин назад - новые входы пропущены", late / 60000)

    st["last_bar_t"] = bar_t
    _save(st)


# ---------------- очередь постов ----------------

def pending_event(now_ts: float | None = None):
    """Самое раннее непросроченное событие (выходы приоритетнее) или None.
    Просроченные удаляет."""
    now_ts = now_ts or time.time()
    st = _state()
    fresh = []
    for ev in st["outbox"]:
        ttl = ENTRY_EVENT_TTL_S if ev["kind"] == "entry" else EXIT_EVENT_TTL_S
        if now_ts - ev["ts"] <= ttl:
            fresh.append(ev)
    if len(fresh) != len(st["outbox"]):
        st["outbox"] = fresh
        _save(st)
    if not fresh:
        return None
    exits = [e for e in fresh if e["kind"] == "exit"]
    return (exits or fresh)[0]


def mark_posted(ev: dict) -> None:
    st = _state()
    st["outbox"] = [e for e in st["outbox"] if not (e["kind"] == ev["kind"] and e["ticker"] == ev["ticker"] and e["ts"] == ev["ts"])]
    _save(st)


def live_stats() -> dict:
    closed = _state()["closed"]
    n = len(closed)
    if n == 0:
        return {"n": 0, "wins": 0, "avg": 0.0}
    return {"n": n, "wins": sum(1 for c in closed if c["pnl"] > 0), "avg": sum(c["pnl"] for c in closed) / n}


# ---------------- график ----------------

def _to_candles(bars: list) -> list:
    return [{"open_time": b.t, "open": b.o, "high": b.h, "low": b.l, "close": b.c, "volume": b.v} for b in bars]


def build_chart(ev: dict, fetch=None):
    """Картинка к посту: вход (линия входа, стартовый стоп, уровень пробоя) или
    итог (вход/выход). Путь к PNG или None - пост тогда уйдёт без картинки."""
    try:
        import chart_generator
        bars = (fetch or fetch_bars)(ev["ticker"])
        if len(bars) < 40:
            return None
        candles = _to_candles(bars)
        if ev["kind"] == "entry":
            return chart_generator.generate_trade_chart(
                ev["ticker"], candles[-70:], entry=ev["entry"], stop=ev["stop"], level=ev.get("level"),
                title="Breakout idea", entry_t=ev.get("entry_t"),
            )
        entry_t = ev.get("entry_t") or candles[-1]["open_time"]
        exit_t = entry_t + (ev["held"] - 1) * BAR_MS
        i_entry = next((i for i, c in enumerate(candles) if c["open_time"] >= entry_t), len(candles) - 1)
        i_exit = next((i for i, c in enumerate(candles) if c["open_time"] >= exit_t), len(candles) - 1)
        start = max(0, i_entry - 25)
        window = candles[start:min(len(candles), i_exit + 8)]
        return chart_generator.generate_trade_chart(
            ev["ticker"], window[-110:], entry=ev["entry"], stop=ev.get("stop0"), exit_price=ev["exit"],
            title=f"Closed {ev['pnl']:+.1f}% after fees", entry_t=entry_t, exit_t=exit_t, suffix="_tb_exit",
        )
    except Exception:
        logger.exception("Пробой 20 дней: не удалось построить график для %s", ev.get("ticker"))
        return None


# ---------------- тексты ----------------

def _fmt(x: float) -> str:
    return f"{x:.6g}"


def record_line(stats: dict, lang: str) -> str:
    if lang == "ru":
        if stats["n"] == 0:
            return "Живой счёт метода: закрытых сделок пока нет, считаем с этого поста."
        return (f"Живой счёт метода: закрыто {stats['n']}, в плюс {stats['wins']}, "
                f"средний итог {stats['avg']:+.2f}% на сделку после комиссий.")
    if stats["n"] == 0:
        return "Live record of this method: no closed trades yet, counting starts with this post."
    return (f"Live record of this method: {stats['n']} closed, {stats['wins']} in profit, "
            f"average {stats['avg']:+.2f}% per trade after fees.")


def build_text(ev: dict, stats: dict | None = None, lang: str | None = None) -> str:
    lang = lang or config.SQUARE_TEMPLATE_LANG
    stats = stats if stats is not None else live_stats()
    t = ev["ticker"]
    tags = f"#{t} #Breakout"
    rec = record_line(stats, lang)
    if ev["kind"] == "entry":
        if lang == "ru":
            return (
                f"${t}: пробил максимум за 20 дней на 4-часовом графике.\n\n"
                f"Идея в лонг от {_fmt(ev['entry'])}. Стартовый стоп {_fmt(ev['stop'])} (−{ev['risk_pct']:.1f}%).\n"
                f"Цели нет: стоп подтягиваем за ценой, максимум держим 5 суток.\n"
                f"В истории за 3 года такой метод выигрывает примерно в 4 сделках из 10, серии убытков нормальны.\n\n"
                f"{rec}\n\nНе финансовый совет. {tags}"
            )
        return (
            f"${t} just broke its 20-day high on the 4h chart.\n\n"
            f"Idea: long from {_fmt(ev['entry'])}. Starting stop {_fmt(ev['stop'])} (-{ev['risk_pct']:.1f}%).\n"
            f"No fixed target: the stop trails behind price, max hold 5 days.\n"
            f"Over 3 years of history this method wins about 4 trades in 10, losing streaks are normal.\n\n"
            f"{rec}\n\nNot financial advice. {tags}"
        )
    days = ev["held"] * 4 / 24
    if lang == "ru":
        why = "сработал подтянутый стоп" if ev["reason"] == "stop" else "вышли по времени (5 суток)"
        return (
            f"${t}: идея закрыта, {ev['pnl']:+.1f}% после комиссий за {days:.1f} сут.\n"
            f"Вход {_fmt(ev['entry'])}, выход {_fmt(ev['exit'])} ({why}).\n\n"
            f"{rec}\n\nПубликуем и плюсы, и минусы. Не финансовый совет. {tags}"
        )
    why = "the trailing stop was hit" if ev["reason"] == "stop" else "time limit reached (5 days)"
    return (
        f"${t}: idea closed, {ev['pnl']:+.1f}% after fees over {days:.1f} days.\n"
        f"Entry {_fmt(ev['entry'])}, exit {_fmt(ev['exit'])} ({why}).\n\n"
        f"{rec}\n\nWe post the wins and the losses. Not financial advice. {tags}"
    )
