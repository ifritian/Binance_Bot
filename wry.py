"""Формат "Wry": редкая сухая шутка по поводу реальной ситуации (рынок, наш собственный демо-счёт, Level Check).

Юмор - сухой (deadpan), через преуменьшение, неожиданное сравнение и самоиронию бота. Правила:
- шутим над собой, ситуацией и рынком, не над людьми, не над чужими потерями и не на фоне паники (при сильных обвалах формат молчит);
- без прогнозов и призывов, шутка не объясняется;
- цифры только настоящие (подставляются в шаблон), никакой выдумки про "знакомых трейдеров" и личные истории;
- редко: не чаще раза в ~2 дня, в паре с cashtag $BTC (иначе нет виджета цены).
"""
import random

KEY = "wry_log"

FLAT = [
    "$BTC moved {b24} in 24 hours. I have seen more drama in a spreadsheet footnote.",
    "Market update: nothing happened. $BTC {b24}. I looked twice, then a third time, out of respect.",
    "$BTC {b24} today. Somewhere a volatility trader is staring at a wall and slowly accepting it.",
    "$BTC {b24}. The candles are so small I checked if the exchange was still open. It is. It just does not feel like talking.",
    "Crypto is {b24} today and everyone is suddenly calm and reasonable. Suspicious.",
    "A full day of $BTC at {b24}. If this were a movie, it would be the part where the main character does laundry.",
]
OWN_LOSS = [
    "My demo futures bot just lost {n} trades in a row. It is play money and it is still taking it personally. $BTC",
    "Demo account update: {n} stops in a row. The bot calls it 'a data point'. The bot calls everything that. $BTC",
    "{n} losing demo trades in a row. I wanted to call it a rough patch, but every trade is public, so: it is a losing streak. $BTC",
    "The strategy and the market are no longer on speaking terms. Demo account, {n} losses in a row, all of it logged. $BTC",
    "Good news: the demo bot is consistent. Bad news: this week it is consistent at {n} losses in a row. $BTC",
]
LEVEL_BAD = [
    "Level Check: the level held {held} times out of {total}. Support lines are basically horoscopes with a y-axis. $BTC",
    "{held} of {total}. A coin flip would have been more consistent and much cheaper. $BTC",
    "Level Check score: {held} out of {total}. The market treats support levels like polite suggestions. $BTC",
]
LEVEL_GOOD = [
    "Level Check: held {held} of {total} times. I would like to thank the market for letting me look smart for a moment. $BTC",
    "{held} out of {total} levels held. Enjoy it, because the market will read this post and adjust. $BTC",
]
FEAR = [
    "Fear & Greed Index: {v} ({label}). Everyone says they are calm. The index has seen their search history. $BTC",
    "The Fear & Greed Index says {v}. I say we all agree to be long-term investors until Thursday. $BTC",
    "Fear & Greed at {v} ({label}). Every group chat has quietly become a support group. $BTC",
]
GREED = [
    "Fear & Greed at {v} ({label}). Every group chat has become a finance podcast. $BTC",
    "The Fear & Greed Index is {v}. Confidence is high, and so is the number of people explaining why it is different this time. $BTC",
]
WEEKEND = [
    "It is the weekend. The market is open. So is my inability to log off. $BTC {b24} in 24h.",
    "$BTC trades 24/7, which means the market has never once let me finish a thought. {b24} in 24h.",
]


def situation(ctx: dict) -> tuple | None:
    """ctx: b24 (BTC 24ч, %), fng {value,label}|None, loss_streak (int), level {held,total,fresh}|None, weekend (bool).
    -> (имя, поля) или None. При сильном движении рынка шутить не стоит."""
    b24 = ctx.get("b24")
    if b24 is None or abs(b24) >= 4:
        return None
    f = {"b24": f"{b24:+.1f}%".replace("-", "−")}
    if ctx.get("loss_streak", 0) >= 3:
        return "own_loss", {**f, "n": ctx["loss_streak"]}
    lv = ctx.get("level")
    if lv and lv.get("fresh") and lv["total"] >= 5:
        held, total = lv["held"], lv["total"]
        return ("level_bad" if held / total <= 0.4 else "level_good" if held / total >= 0.7 else "skip"), {**f, "held": held, "total": total}
    fng = ctx.get("fng")
    if fng and fng["value"] <= 22:
        return "fear", {**f, "v": fng["value"], "label": fng["label"]}
    if fng and fng["value"] >= 78:
        return "greed", {**f, "v": fng["value"], "label": fng["label"]}
    if abs(b24) <= 0.8:
        return "flat", f
    if ctx.get("weekend"):
        return "weekend", f
    return None


_POOLS = {"flat": FLAT, "own_loss": OWN_LOSS, "level_bad": LEVEL_BAD, "level_good": LEVEL_GOOD, "fear": FEAR, "greed": GREED, "weekend": WEEKEND}


def build(ctx: dict, recent_heads: list | None = None, rng=None) -> str | None:
    rng = rng or random
    sit = situation(ctx)
    if sit is None or sit[0] not in _POOLS:
        return None
    name, fields = sit
    options = _POOLS[name]
    heads = [(h or "")[:30].lower() for h in (recent_heads or [])]
    fresh = [o for o in options if o.format(**fields)[:30].lower() not in heads]
    line = rng.choice(fresh or options).format(**fields)
    if "$BTC" not in line:
        line += " $BTC"
    return line + "\n\nNot financial advice.\n\n#Crypto"
