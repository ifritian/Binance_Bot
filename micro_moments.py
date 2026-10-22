"""Микромоменты: короткие строки, которые делают ленту живой и при этом остаются правдой.

Только то, что можно проверить: календарь (день недели, экспирация, открытие США), серия дней индекса
страха и жадности, итог по ранее показанным уровням монеты. Никаких выдуманных личных историй,
причинных заявлений ("из-за") и обещаний - всегда осторожные формулировки.
"""
import random
from datetime import date, datetime, timedelta, timezone


def _us_dst(d: date) -> bool:
    """Летнее время США: со 2-го воскресенья марта по 1-е воскресенье ноября."""
    def nth_sunday(y, m, n):
        first = date(y, m, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    return nth_sunday(d.year, 3, 2) <= d < nth_sunday(d.year, 11, 1)


def us_open_utc(d: date) -> tuple[int, int]:
    return (13, 30) if _us_dst(d) else (14, 30)


def context_line(now: datetime, rng: random.Random | None = None) -> str | None:
    """Одна календарная строка или None. Выбор случайный среди подходящих."""
    rng = rng or random
    d = now.date()
    options = []
    if now.weekday() >= 5:
        options += ["Weekend liquidity tends to be thin, so moves can look bigger than they are.",
                    "It's the weekend: fewer traders around, so prices can jump on small orders."]
    if now.weekday() == 0:
        options += ["A new trading week is starting.", "Monday: the week's first full day of trading."]
    if now.weekday() == 4:
        options += ["Friday is expiry day for many weekly crypto options (08:00 UTC)."]
    if now.weekday() < 5:
        h, m = us_open_utc(d)
        mins = h * 60 + m - (now.hour * 60 + now.minute)
        if 0 < mins <= 240:
            options += [f"US stocks open at {h:02d}:{m:02d} UTC, and crypto sometimes moves around that time."]
    if (d + timedelta(days=2)).month != d.month:
        options += ["Month-end is close; some funds rebalance around now, which can add volatility."]
    return rng.choice(options) if options else None


_BANDS = [(24, "extreme fear"), (44, "fear"), (55, "neutral"), (74, "greed"), (100, "extreme greed")]


def _band(v: int) -> str:
    return next(name for top, name in _BANDS if v <= top)


def fng_streak_line(history: list[int] | None, rng: random.Random | None = None) -> str | None:
    """history - значения индекса, НОВЫЕ ПЕРВЫМИ (сегодня, вчера, ...). Серия дней в одной зоне
    (>= 3) или экстремум за последние >= 14 дней."""
    rng = rng or random
    if not history or len(history) < 3:
        return None
    cur = _band(history[0])
    n = 1
    for v in history[1:]:
        if _band(v) != cur:
            break
        n += 1
    if n >= 3:
        return rng.choice([f"That's day {n} in a row in {cur} territory.", f"{n} days running in {cur}."])
    if len(history) >= 14:
        v, rest = history[0], history[1:]
        if v < min(rest):
            k = len(rest) + 1
            return f"Lowest reading in {k} days."
        if v > max(rest):
            return f"Highest reading in {len(rest) + 1} days."
    return None


def followup_record(ev: dict, lv: dict | None, ts: float) -> dict:
    """Запоминаем, что показали в посте про скачок, чтобы позже честно сверить с рынком."""
    return {"ticker": ev["ticker"], "price": ev["price"], "ts": ts,
            "hi": lv["hi"] if lv else None, "lo": lv["lo"] if lv else None, "done": False}


def followup_due(rec: dict, now_ts: float, min_h: float = 6, max_h: float = 30) -> bool:
    age = (now_ts - rec["ts"]) / 3600
    return not rec.get("done") and min_h <= age <= max_h


def followup_text(rec: dict, price_now: float, px, rng: random.Random | None = None) -> str:
    """Итог по ранее показанным уровням. px - форматтер цены. Ровно один $тикер."""
    rng = rng or random
    t, p0 = rec["ticker"], rec["price"]
    chg = (price_now / p0 - 1) * 100
    hi, lo = rec.get("hi"), rec.get("lo")
    if hi is not None and price_now >= hi:
        verdict = f"Price went above {px(hi)}, the level bulls needed."
    elif lo is not None and price_now <= lo:
        verdict = f"Price went below {px(lo)}, the level bears were targeting."
    elif hi is not None:
        verdict = f"Neither level broke: it is still between {px(lo)} and {px(hi)}."
    else:
        verdict = ""
    opener = rng.choice([f"Checking back on ${t}.", f"Update on ${t} from earlier.", f"Back to ${t}, which I posted about earlier."])
    line2 = f"Then {px(p0)}, now {px(price_now)} ({chg:+.1f}%)".replace("-", "−")
    out = [opener, "", line2 + ("." if not verdict else "."), verdict] if verdict else [opener, "", line2 + "."]
    out += ["", rng.choice(["Mapping both sides only helps if I check the result, so here it is, whichever way it went.",
                            "I log these either way: the map is only worth something if the result gets published too.",
                            "Posting the result whether it worked or not."]),
            "", "Not financial advice.", "", f"#{t}"]
    return "\n".join(out)
