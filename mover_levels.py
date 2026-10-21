"""
Пост "монета резко двинулась" - наблюдение с уровнями, без торговых призывов.

Почему не вход/стоп/цель: проверка на истории (backtest_movers.py, 59 монет, год, час-свечи,
комиссии) показала, что правило "войти по движению со стопом 1.5 ATR и целью 2R" в среднем
теряет (-0.24% на сделку, t = -4.4), а обратное тоже не работает. Публиковать как "идею" то, что
в среднем проигрывает, нельзя, поэтому пост описывает, что произошло и где ключевые уровни.
"""
import random

import mover_setups


def build_levels(ticker: str, price: float, pct24: float, bars_1h: list) -> dict | None:
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
    return {"ticker": ticker, "price": price, "pct24": pct24, "hi": hi, "lo": lo, "mid": (hi + lo) / 2,
            "pos": (price - lo) / (hi - lo), "vol_ratio": vol_ratio}


def build_text(lv: dict, rng=None) -> str:
    rng = rng or random
    t, up = lv["ticker"], lv["pct24"] > 0
    px = mover_setups._px
    lead = ("🚀" if up else "🔻") if rng.random() < 0.6 else ""
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
    lines += ["", rng.choice([
        f"Levels people watch: {px(lv['hi'])} (24h high), {px(lv['mid'])} (middle), {px(lv['lo'])} (24h low).",
        f"The range edges are {px(lv['hi'])} and {px(lv['lo'])}; the middle is {px(lv['mid'])}.",
        f"Key levels: high {px(lv['hi'])}, midpoint {px(lv['mid'])}, low {px(lv['lo'])}."])]
    lines.append(rng.choice(["A move like this can continue or fade, nobody knows which.",
                             "Moves like this are as likely to fade as to continue.",
                             "No edge claimed here, just what the chart shows."]))
    ask = rng.choice(["What's your read?", "Continuation or reversal?", "", ""])
    if ask:
        lines += ["", ask]
    lines += ["", rng.choice(["Not financial advice.", "NFA.", "Not a recommendation."]), "",
              rng.choice([f"#{t} #Crypto", f"#{t}", f"#{t} #Altcoins"])]
    return "\n".join(lines)
