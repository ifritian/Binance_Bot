"""Формат "Quick setup": короткий пост-мини: одна строка введения, вход, стоп, цель, дисклеймер, ссылка-тикер и хэштеги.

Частый формат (до нескольких в день), срабатывает на любом заметном движении ликвидной монеты или мем-коина, а не только на
экстремумах. График - с пометками "от руки" (стрелки, кружок на входе, подписи) и строкой с доп. информацией (24ч, RSI).
Каждая идея ведётся в трекере вместе с теневыми вариантами стопа/цели (mover_setups), итоги публикуются, убытки тоже.
Честная оговорка: по годовому бэктесту такие идеи на небольших движениях в среднем не прибыльны, поэтому формат помечен
отдельным вариантом (q...) и его результат считается отдельно - по накопленной статистике решаем, оставлять ли его."""
import random

import mover_setups as ms
import trade_idea

QUICK_MOMENTUM = {"stop_atr": 2.0, "rr": 1.0, "hold_h": 12, "name": "q2.0/1.0/12h"}
QUICK_FADE = {"stop_atr": 3.0, "rr": 2.0, "hold_h": 72, "name": "qf3.0/2.0/72h"}
MIN_MOVE, MIN_MOVE_MEME = 4.0, 3.0
RSI_HIGH, RSI_LOW = 80.0, 20.0


def threshold(ticker: str) -> float:
    return MIN_MOVE_MEME if trade_idea.is_meme(ticker) else MIN_MOVE


def build(ticker: str, price: float, pct24: float, bars_1h: list, rsi: float | None) -> dict | None:
    """Идея за движением; если часовой RSI уже в экстремуме и движение >= 5% - идея против движения. None, если движение мало или нет ATR."""
    if abs(pct24) < threshold(ticker):
        return None
    extreme = rsi is not None and (rsi >= RSI_HIGH or rsi <= RSI_LOW) and abs(pct24) >= 5.0
    if extreme:
        side = "short" if rsi >= RSI_HIGH else "long"
        return ms.build_setup(ticker, price, pct24, bars_1h, side=side, mode="fade", variant=QUICK_FADE)
    return ms.build_setup(ticker, price, pct24, bars_1h, mode="momentum", variant=QUICK_MOMENTUM)


def rsi_info(rsi: float | None) -> dict | None:
    if rsi is None or not (rsi >= RSI_HIGH or rsi <= RSI_LOW):
        return None
    return {"rsi": rsi, "kind": "high" if rsi >= RSI_HIGH else "low"}


def build_text(setup: dict, tone: str | None = None, rsi: dict | None = None, rng=None) -> str:
    rng = rng or random
    t = setup["ticker"]
    tone = tone or trade_idea.pick_tone(t, None, rsi["kind"] if rsi else None, rng)
    hook = trade_idea._hook(setup, tone, rsi, rng)
    px = ms._px
    risk, rr = setup["dist_pct"], setup.get("rr", 1.0)
    tp_sign, st_sign = ("+", "-") if setup["side"] == "long" else ("-", "+")
    lines = [hook, "",
             f"{'Long' if setup['side'] == 'long' else 'Short'} entry: {px(setup['entry'])}",
             f"Stop-loss: {px(setup['stop'])} ({st_sign}{risk:.1f}%)",
             f"Take-profit: {px(setup['target'])} ({tp_sign}{risk * rr:.1f}%)",
             f"R:R 1:{rr:g}, valid {setup.get('hold_h', 12)}h",
             "", "Not financial advice.", "", f"${t} #{t} #Crypto"]
    return "\n".join(lines)


def chart_title(setup: dict, rsi: float | None, vol_x: float | None = None) -> str:
    parts = ["Quick setup", f"24h {setup['pct24']:+.1f}%"]
    if rsi is not None:
        parts.append(f"RSI {rsi:.0f}")
    if vol_x is not None and vol_x >= 1.5:
        parts.append(f"vol {vol_x:.1f}x")
    return " · ".join(parts)


def pick_candidates(rows: list, allowed: set, log: dict, now: float, recent_text: str, meme_min_vol: float, major_min_vol: float,
                    cooldown_h: float = 10.0) -> list:
    """[(мем?, -|pct|, тикер, цена, pct24)] отсортировано: мем-коины первыми, затем по силе движения."""
    out = []
    for r in rows:
        sym = r.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        t = sym[:-4]
        if t not in allowed or f"${t}" in recent_text:
            continue
        if now - log.get(t, 0) < cooldown_h * 3600:
            continue
        try:
            vol, pct, price = float(r["quoteVolume"]), float(r["priceChangePercent"]), float(r["lastPrice"])
        except (KeyError, ValueError, TypeError):
            continue
        meme = trade_idea.is_meme(t)
        if vol < (meme_min_vol if meme else major_min_vol) or abs(pct) < threshold(t):
            continue
        out.append((0 if meme else 1, -abs(pct), t, price, pct))
    out.sort()
    return out
