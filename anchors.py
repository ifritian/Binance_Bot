"""Якорные дни: запланированные события, на которые рынок смотрит (решение ФРС, инфляция, занятость, экспирация опционов).

Пост "до" за сутки: когда событие и как BTC вёл себя в 2 часа после прошлых решений ФРС этого года (считаем из свечей).
Пост "после" через 2-8 часов: что реально произошло с BTC. Причину не утверждаем: события на рынке совпадают во времени.
Даты сверены с публичными календарями (ФРС, BLS) 24 окт 2026; календарь надо обновлять раз в квартал.
"""
import random
from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def _dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


# kind: fomc / cpi / jobs / expiry. Время США: до 1 ноября 2026 EDT (UTC-4), после - EST (UTC-5).
EVENTS = [
    {"id": "fomc-2026-10", "kind": "fomc", "name": "US Federal Reserve rate decision", "t": _dt(2026, 10, 28, 18)},
    {"id": "jobs-2026-11", "kind": "jobs", "name": "US jobs report", "t": _dt(2026, 11, 6, 13, 30)},
    {"id": "cpi-2026-11", "kind": "cpi", "name": "US inflation (CPI) report", "t": _dt(2026, 11, 10, 13, 30)},
    {"id": "jobs-2026-12", "kind": "jobs", "name": "US jobs report", "t": _dt(2026, 12, 4, 13, 30)},
    {"id": "fomc-2026-12", "kind": "fomc", "name": "US Federal Reserve rate decision", "t": _dt(2026, 12, 9, 19)},
    {"id": "cpi-2026-12", "kind": "cpi", "name": "US inflation (CPI) report", "t": _dt(2026, 12, 10, 13, 30)},
]
PAST_FOMC = [_dt(2026, 1, 28, 19), _dt(2026, 3, 18, 18), _dt(2026, 4, 29, 18), _dt(2026, 6, 17, 18), _dt(2026, 7, 29, 18), _dt(2026, 9, 16, 18)]


def expiries(year: int, months=(10, 11, 12)) -> list[dict]:
    """Месячная экспирация опционов Deribit: последняя пятница месяца, 08:00 UTC."""
    out = []
    for m in months:
        d = _dt(year + (m // 13), m % 12 + 1 if m == 12 else m + 1, 1, 8) - timedelta(days=1) if m < 12 else _dt(year, 12, 31, 8)
        while d.weekday() != 4:
            d -= timedelta(days=1)
        name = "Quarterly options expiry on Deribit" if m % 3 == 0 else "Monthly options expiry on Deribit"
        out.append({"id": f"expiry-{year}-{m:02d}", "kind": "expiry", "name": name, "t": d.replace(hour=8, minute=0)})
    return out


def all_events(year: int = 2026) -> list[dict]:
    return sorted(EVENTS + expiries(year), key=lambda e: e["t"])


def due(now: datetime, done: list, events: list | None = None) -> tuple[str, dict] | None:
    """('pre'|'post', событие) или None. pre: за 14-30 ч до; post: через 2-8 ч после."""
    for e in events or all_events(now.year):
        dt = (e["t"] - now).total_seconds() / 3600
        if 14 <= dt <= 30 and f"pre:{e['id']}" not in done:
            return "pre", e
        if -8 <= dt <= -2 and f"post:{e['id']}" not in done:
            return "post", e
    return None


def reaction(bars: list) -> dict | None:
    """bars: 15-минутные свечи от момента события: {'move': %, 'range': %}."""
    if len(bars) < 4:
        return None
    o = bars[0][1]
    return {"move": (bars[-1][4] / o - 1) * 100, "range": (max(b[2] for b in bars) / min(b[3] for b in bars) - 1) * 100}


def history_summary(reactions: list) -> dict | None:
    r = [x for x in reactions if x]
    if len(r) < 3:
        return None
    return {"n": len(r), "avg_abs": sum(abs(x["move"]) for x in r) / len(r), "biggest": max(abs(x["move"]) for x in r),
            "up": sum(1 for x in r if x["move"] > 0)}


def _pct(x: float) -> str:
    return f"{x:+.1f}%".replace("-", "−")


def pre_text(e: dict, btc: float | None, hist: dict | None, px, rng: random.Random | None = None) -> str:
    rng = rng or random
    when = e["t"].strftime("%b %d, %H:%M UTC")
    head = rng.choice([f"Tomorrow, {when}: {e['name']}.", f"On the calendar: {e['name']}, {when}."])
    out = [head, ""]
    if btc:
        out.append(f"$BTC is at {px(btc)} now.")
    if hist and e["kind"] == "fomc":
        out.append(f"In the {hist['n']} Fed decisions so far this year, BTC moved {hist['avg_abs']:.1f}% on average in the two hours after "
                   f"(biggest {hist['biggest']:.1f}%) and finished higher in {hist['up']} of {hist['n']}.")
        out.append(f"{hist['n']} events is a small sample, so treat it as context, not a pattern.")
    elif e["kind"] == "expiry":
        out.append("Large options expiries can shake prices around the settlement time, or do nothing at all.")
    else:
        out.append("Scheduled data often means sharper moves around the release, in either direction.")
    out += ["", "I will post what BTC actually did afterwards, whichever way it goes.", "", "Not financial advice.", "", f"#{e['kind'].upper() if e['kind'] != 'fomc' else 'FOMC'}"]
    return "\n".join(out)


def post_text(e: dict, r: dict, now_move: float | None, hist: dict | None) -> str:
    lines = [f"{e['name']}: what $BTC did.", "",
             f"First 2 hours after {e['t'].strftime('%H:%M UTC')}: {_pct(r['move'])}, with a {r['range']:.1f}% high-to-low swing."]
    if now_move is not None:
        lines.append(f"Now, versus the release: {_pct(now_move)}.")
    if hist:
        lines.append(f"For scale, the typical 2-hour move after Fed decisions this year was {hist['avg_abs']:.1f}%.")
    lines += ["", "Moves right after a release are only loosely linked to it: many things hit the market at once.", "",
              "Not financial advice.", "", "#BTC"]
    return "\n".join(lines)
