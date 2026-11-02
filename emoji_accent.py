"""Эмодзи-акцент в начале поста: примерно в каждом третьем посте, разные по смыслу и по набору.

Правила: не больше одного акцента на 2-3 поста подряд (чтобы не превращать ленту в ёлку), не ставим, если пост уже
начинается с эмодзи, и не трогаем видео/подписи. Знак подбирается по смыслу: тревога, рост/падение, нейтральный взгляд, шутка.
"""
import random
import re

ALERT = ["🛑", "🚨", "❗", "‼️", "🔴"]
DOWN = ["🔻", "🔴", "📉", "⚠️"]
UP = ["🟢", "📈", "🔥", "⚡"]
NEUTRAL = ["👀", "📌", "⚡", "🧭"]
WRY = ["🫠", "😅", "🙃", "🤷"]
NEVER = {"video", "video_map", "video_replay", "listing_report", "weekly_brief"}
ALERT_FORMATS = {"market_shock", "binance_breaking", "breaking_news"}
_DOWN_RE = re.compile(r"\b(down|dump\w*|bleed\w*|red|drop\w*|delist\w*|crash\w*|falls?|fell|lower|sell-?off)\b|−\d", re.IGNORECASE)
_UP_RE = re.compile(r"\b(up|pump\w*|rall\w+|green|ripping|jump\w*|higher|surge\w*)\b|\+\d", re.IGNORECASE)
_EMOJI_START = re.compile(r"^[☀-➿\U0001F000-\U0001FAFF‼⁉⬀-⯿]")


def pool_for(fmt: str, text: str) -> list:
    if fmt == "wry":
        return WRY
    if fmt in ALERT_FORMATS:
        return ALERT if _DOWN_RE.search(text[:200]) or fmt != "market_shock" else UP
    head = text[:200]
    down, up = bool(_DOWN_RE.search(head)), bool(_UP_RE.search(head))
    if down and not up:
        return DOWN
    if up and not down:
        return UP
    return NEUTRAL


def should_apply(fmt: str, history: list, rng=None) -> bool:
    """history - последние флаги (True = был акцент). Срочные форматы получают акцент чаще, шутки - почти всегда."""
    rng = rng or random
    if fmt in NEVER:
        return False
    if fmt in ALERT_FORMATS:
        return rng.random() < 0.7 and not (len(history) >= 2 and all(history[-2:]))
    if fmt == "wry":
        return rng.random() < 0.8
    if any(history[-2:]):
        return False            # не два акцента подряд и не чаще чем раз в три поста
    return rng.random() < 0.5 or (len(history) >= 4 and not any(history[-4:]))


def apply(fmt: str, text: str, rng=None) -> str:
    rng = rng or random
    if not text or _EMOJI_START.match(text.lstrip()):
        return text
    pool = pool_for(fmt, text)
    mark = rng.choice(pool)
    if fmt in ALERT_FORMATS and mark in ("🛑", "🚨", "🔴") and rng.random() < 0.4:
        mark = mark * 2
    return f"{mark} {text}"
