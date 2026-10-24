"""Серия "Level Check #N": раз в сутки диапазон монеты за 24ч (верх/низ) и итог вчерашнего диапазона.

Без прогнозов: показываем границы и честно проверяем, удержался ли вчерашний диапазон. Ведётся счётчик
"удержался / пробит" - его публикуем при любом результате.
"""
import random

ROTATION = ["BTC", "ETH", "SOL", "BNB"]
KEY = "level_check"


def pick_ticker(n: int) -> str:
    return ROTATION[(n - 1) % len(ROTATION)]


def compute(bars: list) -> dict | None:
    """bars: [(t,o,h,l,c,v)] 1h; берём последние 24."""
    b = bars[-24:]
    if len(b) < 12:
        return None
    return {"hi": max(x[2] for x in b), "lo": min(x[3] for x in b), "price": b[-1][4]}


def evaluate(rec: dict, bars: list) -> str:
    """'held' / 'above' / 'below' / 'both' по свечам после публикации."""
    after = [x for x in bars if x[0] >= rec["ts"] * 1000]
    if not after:
        return "held"
    up = max(x[2] for x in after) > rec["hi"]
    dn = min(x[3] for x in after) < rec["lo"]
    return "both" if up and dn else "above" if up else "below" if dn else "held"


def record(hist: list) -> tuple[int, int]:
    return sum(1 for h in hist if h["res"] == "held"), len(hist)


def build_text(n: int, ticker: str, lv: dict, px, prev: dict | None, hist: list,
               rng: random.Random | None = None) -> str:
    rng = rng or random
    out = [f"Level Check #{n}: ${ticker}", "",
           f"Now {px(lv['price'])}", f"24h high {px(lv['hi'])}", f"24h low {px(lv['lo'])}", ""]
    if prev:
        words = {"held": "stayed inside its range", "above": "broke above its range",
                 "below": "broke below its range", "both": "broke out on both sides"}
        out += [f"Yesterday's check (#{n - 1}, {prev['ticker']}): it {words[prev['res']]} "
                f"({px(prev['lo'])} to {px(prev['hi'])}).".replace("(#", "(#")]
        held, tot = record(hist)
        out += [f"Series record: range held {held} of {tot} days.", ""]
    out += [rng.choice(["These are just the edges of the last day, not a forecast.",
                        "No prediction here, only where the edges are.",
                        "Edges only. I check them again tomorrow, whichever way it goes."]),
            rng.choice(["Does the range hold today?", "Inside or outside by tomorrow?"]),
            "", "Not financial advice.", "", "#LevelCheck"]
    return "\n".join(out)
