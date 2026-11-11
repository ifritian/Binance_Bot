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
MIN_MOVE = 6.0            # резкое движение за 24ч у ЛЮБОЙ монеты
MIN_MOVE_RSI = 3.0        # при RSI-экстремуме достаточно меньшего движения
RSI_HIGH, RSI_LOW = 80.0, 20.0
SKIP_BASES = frozenset({"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USD1", "EUR", "EURI", "AEUR", "BFUSD", "USDE", "XUSD", "PAXG", "XAUT", "WBTC", "WBETH", "BNSOL"})


def threshold(ticker: str, rsi: float | None = None) -> float:
    return MIN_MOVE_RSI if (rsi is not None and (rsi >= RSI_HIGH or rsi <= RSI_LOW)) else MIN_MOVE


def build(ticker: str, price: float, pct24: float, bars_1h: list, rsi: float | None) -> dict | None:
    """Идея за движением; если часовой RSI в экстремуме (>=80 / <=20) - идея против движения.
    None, если движение мало или нет ATR."""
    if abs(pct24) < threshold(ticker, rsi):
        return None
    extreme = rsi is not None and (rsi >= RSI_HIGH or rsi <= RSI_LOW)
    if extreme:
        side = "short" if rsi >= RSI_HIGH else "long"
        return ms.build_setup(ticker, price, pct24, bars_1h, side=side, mode="fade", variant=QUICK_FADE)
    return ms.build_setup(ticker, price, pct24, bars_1h, mode="momentum", variant=QUICK_MOMENTUM)


def rsi_info(rsi: float | None) -> dict | None:
    if rsi is None or not (rsi >= RSI_HIGH or rsi <= RSI_LOW):
        return None
    return {"rsi": rsi, "kind": "high" if rsi >= RSI_HIGH else "low"}


def hook(ticker: str, pct: float, rsi: float | None, vol_x: float | None, rng) -> str:
    """Короткий цепляющий заход: красная эмодзи + клише-фраза. Про объём пишем только если он реально вырос."""
    t = ticker
    vol = vol_x is not None and vol_x >= 2.0
    if rsi is not None and rsi >= RSI_HIGH:
        pool = [f"🚨 STOP. ${t} is overheated: RSI {rsi:.0f}, {pct:+.1f}% in 24h. Keep an eye on this coin.",
                f"🔴 Huge surge on ${t}: {pct:+.1f}% and RSI {rsi:.0f}. Take a close look."]
    elif rsi is not None and rsi <= RSI_LOW:
        pool = [f"🚨 STOP. ${t} is crushed: RSI {rsi:.0f}, {pct:+.1f}% in 24h. Keep an eye on this coin.",
                f"🔴 Sharp sell-off on ${t}: {pct:+.1f}% and RSI {rsi:.0f}. Take a close look."]
    elif pct > 0:
        pool = [f"🚨 ${t} just jumped {pct:+.1f}% in 24h. Keep an eye on this coin.",
                f"🔴 STOP. ${t} is ripping: {pct:+.1f}% on the day. Take a close look."]
    else:
        pool = [f"🚨 ${t} just dropped {pct:+.1f}% in 24h. Keep an eye on this coin.",
                f"🔴 STOP. ${t} is sliding hard: {pct:+.1f}% on the day. Take a close look."]
    line = rng.choice(pool)
    if vol:
        line += f" Volume is {vol_x:.0f}x normal."
    return line


def build_text(setup: dict, rsi: float | None = None, vol_x: float | None = None, rng=None) -> str:
    rng = rng or random
    t = setup["ticker"]
    px = ms._px
    risk, rr = setup["dist_pct"], setup.get("rr", 1.0)
    tp_sign, st_sign = ("+", "-") if setup["side"] == "long" else ("-", "+")
    side = "Long" if setup["side"] == "long" else "Short"
    return "\n".join([
        hook(t, setup["pct24"], rsi, vol_x, rng), "",
        f"{side} entry: {px(setup['entry'])}",
        f"Stop-loss: {px(setup['stop'])} ({st_sign}{risk:.1f}%)",
        f"Take-profit: {px(setup['target'])} ({tp_sign}{risk * rr:.1f}%)",
        "", "Not financial advice.", "", f"${t} #{t} #Crypto"])


def chart_title(setup: dict, rsi: float | None, vol_x: float | None = None) -> str:
    parts = [f"24h {setup['pct24']:+.1f}%"]
    if rsi is not None:
        parts.append(f"RSI {rsi:.0f}")
    if vol_x is not None and vol_x >= 2.0:
        parts.append(f"vol {vol_x:.0f}x")
    return "  |  ".join(parts)


def pick_candidates(rows: list, log: dict, now: float, recent_text: str, min_volume: float, cooldown_h: float = 10.0) -> list:
    """[(-|pct|, тикер, цена, pct24)] по убыванию силы движения. Любая USDT-монета с достаточным объёмом, без белого списка."""
    out = []
    for r in rows:
        sym = r.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        t = sym[:-4]
        if not t.isascii() or not t.isalnum() or t in SKIP_BASES or t.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        if f"${t}" in recent_text or now - log.get(t, 0) < cooldown_h * 3600:
            continue
        try:
            vol, pct, price = float(r["quoteVolume"]), float(r["priceChangePercent"]), float(r["lastPrice"])
        except (KeyError, ValueError, TypeError):
            continue
        if vol < min_volume or abs(pct) < MIN_MOVE_RSI:
            continue
        out.append((-abs(pct), t, price, pct))
    out.sort()
    return out
