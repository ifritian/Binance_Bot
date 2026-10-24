"""Сообщество против рынка: итог Telegram-опроса "куда пойдёт монета за 24ч" и счёт толпы."""
KEY_POLLS, KEY_RECORD = "crowd_polls", "crowd_record"
FLAT_PCT = 1.0
MIN_VOTES = 2


def outcome(pct: float) -> str:
    return "Higher" if pct >= FLAT_PCT else "Lower" if pct <= -FLAT_PCT else "About the same"


def tally(poll: dict) -> dict:
    return {o["text"]: int(o.get("voter_count", 0)) for o in poll.get("options", [])}


def crowd_pick(votes: dict) -> tuple[str | None, int]:
    """Выбор толпы среди направленных вариантов; (None, n) при ничьей или нехватке голосов."""
    d = {k: votes.get(k, 0) for k in ("Higher", "Lower", "About the same")}
    n = sum(d.values())
    if n < MIN_VOTES:
        return None, n
    top = max(d.values())
    leaders = [k for k, v in d.items() if v == top]
    return (leaders[0] if len(leaders) == 1 else None), n


def build_text(ticker: str, p0: float, p1: float, votes: dict, rec: dict, px) -> str:
    chg = (p1 / p0 - 1) * 100
    res = outcome(chg)
    pick, n = crowd_pick(votes)
    lines = [f"Community vs market: {ticker}", "",
                f"You voted ({n} directional votes): " + ", ".join(
                    f"{k} {votes.get(k, 0)}" for k in ("Higher", "Lower", "About the same")),
                f"Market, 24h later: {px(p0)} -> {px(p1)} ({chg:+.1f}%), so: {res}."]
    if pick is None:
        lines.append("The crowd was split, so no score this time.")
    else:
        ok = pick == res
        lines.append(f"The crowd said {pick}: {'right' if ok else 'wrong'}.")
    lines += ["", f"Crowd record: {rec['right']} right out of {rec['total']}.", "", "Not financial advice."]
    return "\n".join(lines)
