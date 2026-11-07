"""Формат "Trade idea": вход, стоп и цель - с разным тоном (формальный, шуточный, взрывной).

Основа - те же честные правила, что и в mover_setups (стоп 1.5 ATR, цель 1:2, жизнь идеи 24 ч, итог каждой идеи публикуется,
счёт включает убытки). Меняется только подача:
  formal    - спокойный план: вход, стоп, цель;
  wry       - сухая шутка в первой строке, дальше те же уровни;
  explosive - для экстремального RSI и мем-коинов: короткий громкий заход, чтобы остановить скролл.
Внизу всегда "Not financial advice." и счёт прошлых идей.
"""
import random

import mover_setups as ms

MEME = {"DOGE", "SHIB", "PEPE", "BONK", "WIF", "FLOKI", "TRUMP", "MEME", "PNUT", "POPCAT", "BOME", "NEIRO", "TURBO", "MOG", "BRETT",
        "FARTCOIN", "PENGU", "SPX", "MEW", "GOAT", "ACT", "1000SATS", "1000PEPE", "1000SHIB", "1000BONK", "1000FLOKI", "CHEEMS"}
TONES = ("formal", "wry", "explosive")


def is_meme(ticker: str) -> bool:
    return ticker.upper() in MEME


def pick_tone(ticker: str, last_tone: str | None = None, rsi_kind: str | None = None, rng=None) -> str:
    rng = rng or random
    if rsi_kind or is_meme(ticker):
        weights = {"formal": 0.15, "wry": 0.25, "explosive": 0.60}
    else:
        weights = {"formal": 0.50, "wry": 0.35, "explosive": 0.15}
    if last_tone in weights and len(weights) > 1:
        weights[last_tone] *= 0.25                      # реже повторять тон подряд
    names = list(weights)
    return rng.choices(names, [weights[n] for n in names])[0]


def _levels(setup: dict, s: dict | None, mode_label: str) -> list:
    t, side = setup["ticker"], setup["side"]
    risk = setup["dist_pct"]
    tp_sign, st_sign = ("+", "-") if side == "long" else ("-", "+")
    return [f"Entry: {ms._px(setup['entry'])}",
            f"Stop: {ms._px(setup['stop'])} ({st_sign}{risk:.1f}%)",
            f"Target: {ms._px(setup['target'])} ({tp_sign}{risk * ms.RR:.1f}%)",
            f"{mode_label}. Risk/reward 1:{ms.RR:.0f}. Idea expires after 24h."]


def _hook(setup: dict, tone: str, rsi: dict | None, rng) -> str:
    t, pct, side = setup["ticker"], setup["pct24"], setup["side"]
    up = pct > 0
    meme = is_meme(t)
    if tone == "formal":
        base = f"${t} {pct:+.1f}% in 24h."
        return base + (f" Hourly RSI {rsi['rsi']:.0f}." if rsi else "") + (" Momentum idea:" if setup.get("mode") != "fade" else " Counter-trend idea:")
    if tone == "wry":
        pool = ([f"${t} {pct:+.1f}% today. The chart is sprinting and I am the guy running behind it with a clipboard.",
                 f"${t} {pct:+.1f}%. 'It looks strong' is not a plan. This is a plan:",
                 f"${t} {pct:+.1f}% in 24h. Everyone is staring at it, so here is something to stare at with numbers:"] if up else
                [f"${t} {pct:+.1f}% in 24h. Gravity called. Here is a plan:",
                 f"${t} {pct:+.1f}%. The chart took the stairs down, the elevator was too slow. A plan:",
                 f"${t} {pct:+.1f}% today. Pain is a data point, so here are the levels:"])
        if rsi:
            pool.append(f"${t} RSI {rsi['rsi']:.0f}. The chart has had {'a lot of coffee' if rsi['kind'] == 'high' else 'a very long week'}. A plan:")
        return rng.choice(pool)
    # explosive
    if rsi and rsi["kind"] == "high":
        return rng.choice([f"🚨 ${t} RSI {rsi['rsi']:.0f} on the 1h chart. This is what overheated looks like.",
                           f"🔥 ${t} {pct:+.1f}% and RSI {rsi['rsi']:.0f}. Nothing goes up like this quietly."])
    if rsi:
        return rng.choice([f"🧊 ${t} RSI {rsi['rsi']:.0f} on the 1h chart. Oversold to the floor.",
                           f"⚠️ ${t} {pct:+.1f}% and RSI {rsi['rsi']:.0f}. Panic has a price. Here is the chart's version:"])
    if meme:
        return rng.choice([f"🐸 ${t} {pct:+.1f}% in 24h. Meme coins do not move slowly. Levels:", f"🚀 ${t} {pct:+.1f}%. The memes are awake. A plan with a stop:"]
                           if up else [f"🔻 ${t} {pct:+.1f}% in 24h. The meme is bleeding. Levels:", f"⚠️ ${t} {pct:+.1f}%. Meme coin down hard. A plan with a stop:"])
    return rng.choice([f"🚨 ${t} {pct:+.1f}% in 24h. This one is moving. Levels:"] if up else [f"🚨 ${t} {pct:+.1f}% in 24h. This one is falling. Levels:"])


def build_text(setup: dict, tone: str | None = None, s: dict | None = None, rsi: dict | None = None, rng=None) -> str:
    rng = rng or random
    tone = tone or pick_tone(setup["ticker"], None, rsi["kind"] if rsi else None, rng)
    t, side = setup["ticker"], setup["side"]
    label = (f"{'Long' if side == 'long' else 'Short'} idea" +
             (" against the move" if setup.get("mode") == "fade" else " with the move"))
    closer = {"formal": "", "wry": rng.choice(["I would not bet my lunch on it.", "The chart does not owe anyone anything.", ""]),
              "explosive": rng.choice(["Plan the exit before the entry.", "Size small. Always.", ""])}[tone]
    lines = [_hook(setup, tone, rsi, rng), ""] + _levels(setup, s, label) + [""]
    lines.append(ms.record_line(s))
    if closer:
        lines.append(closer)
    lines += ["Not financial advice.", "", f"#{t} #Crypto"]
    return "\n".join(lines)
