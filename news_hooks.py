"""Первая строка новостных постов: цепляет, но не кричит и не повторяется.

Правила: только факты, которые у нас есть (цена, заголовок); никаких выдуманных причин и прогнозов;
"STOP/BREAKING" - только для действительно срочных форматов, в обычных новостях - спокойные зацепки.
Чтобы не выглядело шаблонно, не берём зацепку, начало которой уже было в недавних постах.
"""
import random


def _signed(p: float) -> str:
    return f"{p:+.1f}%".replace("-", "−")


def _pick(options: list, recent_heads: list, rng) -> str:
    fresh = [o for o in options if not any(o[:22].lower() == (h or "")[:22].lower() for h in recent_heads)]
    return rng.choice(fresh or options)


def coin_hook(ticker: str, pct: float, recent_heads: list | None = None, rng=None) -> str:
    rng = rng or random
    mv = _signed(pct)
    if abs(pct) >= 5:
        opts = [f"${ticker} {mv} today. This headline just landed:", f"{mv} on ${ticker}. Here is the news behind the noise:",
                f"${ticker} is moving fast ({mv}). Read this:", f"Why is ${ticker} {'dumping' if pct < 0 else 'pumping'}? Start with this headline:"]
    else:
        opts = [f"${ticker} {mv} and a headline worth ten seconds:", f"One headline on ${ticker} you should know about:",
                f"${ticker} is {mv} on the day. Then this came out:", f"Small move on ${ticker} ({mv}), bigger story:"]
    return _pick(opts, recent_heads or [], rng)


def market_hook(btc_pct: float, recent_heads: list | None = None, rng=None) -> str:
    rng = rng or random
    opts = ["A headline that could matter for the whole market:", "Worth reading before the next candle:",
            f"$BTC is {_signed(btc_pct)} on the day. Now this:", "Not a coin-specific story. This one is about the whole market:"]
    return _pick(opts, recent_heads or [], rng)


def breaking_hook(recent_heads: list | None = None, rng=None) -> str:
    rng = rng or random
    return _pick(["BREAKING:", "Just in:", "Developing right now:", "Just now:"], recent_heads or [], rng)
