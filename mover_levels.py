"""
Пост "монета резко двинулась" - наблюдение с уровнями, без торговых призывов.

Почему не вход/стоп/цель: проверка на истории (backtest_movers.py, 59 монет, год, час-свечи,
комиссии) показала, что правило "войти по движению со стопом 1.5 ATR и целью 2R" в среднем
теряет (-0.24% на сделку, t = -4.4), а обратное тоже не работает. Публиковать как "идею" то, что
в среднем проигрывает, нельзя, поэтому пост описывает, что произошло и где ключевые уровни.
"""
import random

import mover_setups


def build_levels(ticker: str, price: float, pct24: float, bars_1h: list,
                 fast_pct: float | None = None, fast_min: int | None = None) -> dict | None:
    last = bars_1h[-24:]
    if len(last) < 20 or price <= 0:
        return None
    hi = max(b[2] for b in last)
    lo = min(b[3] for b in last)
    if hi <= lo:
        return None
    vols = [b[5] for b in bars_1h if len(b) > 5]
    vol_ratio = None
    if len(vols) >= 27:
        base = sum(vols[-27:-3]) / 24
        if base > 0:
            vol_ratio = (sum(vols[-3:]) / 3) / base
    rng_h = hi - lo
    return {"ticker": ticker, "price": price, "pct24": pct24, "hi": hi, "lo": lo, "mid": (hi + lo) / 2,
            "up_ext": hi + 0.5 * rng_h, "dn_ext": max(lo - 0.5 * rng_h, lo * 0.5),
            "pos": (price - lo) / rng_h, "vol_ratio": vol_ratio,
            "fast_pct": fast_pct, "fast_min": fast_min}


def build_text(lv: dict, rng=None) -> str:
    rng = rng or random
    t = lv["ticker"]
    up = (lv["fast_pct"] if lv.get("fast_pct") is not None else lv["pct24"]) > 0
    px = mover_setups._px
    lead = ("🚀" if up else "🔻") if rng.random() < 0.6 else ""
    fp = lv.get("fast_pct")
    if fp is not None:
        w = "the last hour" if 50 <= (lv.get("fast_min") or 60) <= 70 else f"the last {lv.get('fast_min')} min"
        head = rng.choice([f"{lead} ${t} {fp:+.1f}% in {w}", f"{lead} ${t} is moving fast: {fp:+.1f}% in {w}",
                           f"{lead} Sharp move in ${t}: {fp:+.1f}% in {w}"]).strip()
        head += f"\n({lv['pct24']:+.1f}% over 24h)"
    else:
        head = rng.choice([f"{lead} ${t} {lv['pct24']:+.1f}% in 24h", f"{lead} ${t} is {lv['pct24']:+.1f}% on the day",
                           f"{lead} Big move in ${t}: {lv['pct24']:+.1f}% over 24h"]).strip()
    pos = lv["pos"]
    where = ("right at the top of its 24h range" if pos > 0.9 else "in the upper part of its 24h range" if pos > 0.6
             else "in the middle of its 24h range" if pos > 0.4 else "in the lower part of its 24h range" if pos > 0.1
             else "right at the bottom of its 24h range")
    lines = [head, "", f"Now {px(lv['price'])}, {where}: {px(lv['lo'])} to {px(lv['hi'])}."]
    vr = lv.get("vol_ratio")
    if vr and vr >= 1.5:
        lines.append(f"Volume over the last 3 hours is {vr:.1f}x the daily hourly average.")
    elif vr and vr <= 0.7:
        lines.append("Volume is fading compared with the rest of the day.")
    pc = lambda a, b: f"{(a / b - 1) * 100:+.1f}%".replace("-", "−")
    up_line = rng.choice([
        f"Bulls: a hold above {px(lv['hi'])} (24h high) opens the way to {px(lv['up_ext'])} ({pc(lv['up_ext'], lv['price'])}).",
        f"If it pushes through {px(lv['hi'])}, the next area is {px(lv['up_ext'])} ({pc(lv['up_ext'], lv['price'])}).",
        f"Upside scenario: break and hold {px(lv['hi'])}, then {px(lv['up_ext'])} ({pc(lv['up_ext'], lv['price'])}) is in view."])
    dn_line = rng.choice([
        f"Bears: losing {px(lv['lo'])} (24h low) points to {px(lv['dn_ext'])} ({pc(lv['dn_ext'], lv['price'])}).",
        f"If {px(lv['lo'])} breaks, the next area is {px(lv['dn_ext'])} ({pc(lv['dn_ext'], lv['price'])}).",
        f"Downside scenario: a close under {px(lv['lo'])} and {px(lv['dn_ext'])} ({pc(lv['dn_ext'], lv['price'])}) comes into play."])
    risk_line = rng.choice([
        f"Either way, the idea is wrong once price closes back through {px(lv['mid'])} against you: that is where many traders place the stop, and size the position so that stop costs about 1% of the account.",
        f"Risk first: pick the level that proves you wrong (the middle, {px(lv['mid'])}, is a common one) and size the trade so being wrong costs a small fixed share of the account.",
        f"Plan the exit before the entry: the stop goes where the idea fails, not where it hurts less. The middle, {px(lv['mid'])}, is one reference."])
    lines += ["", up_line, dn_line, "", risk_line]
    lines.append(rng.choice(["Levels are mechanical (24h range and its extension), not a forecast.",
                             "These are range levels, not a prediction. Moves like this fade as often as they continue.",
                             "No edge claimed: just the range, its extension and where it breaks."]))
    ask = rng.choice(["What's your read?", "Continuation or reversal?", "", ""])
    if ask:
        lines += ["", ask]
    lines += ["", rng.choice(["Not financial advice.", "NFA.", "Not a recommendation."]), "",
              rng.choice([f"#{t} #Crypto", f"#{t}", f"#{t} #Altcoins"])]
    return "\n".join(lines)
