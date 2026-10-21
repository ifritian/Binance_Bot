"""
Разнообразие текстов без LLM: одни и те же данные подаются в разной форме
(заголовок, порядок, длина, знаки препинания, подпись), чтобы посты за день
не выглядели штампом. Источник случайности - rng, который можно задать в тестах.
"""
import random

DISCLAIMERS = [
    "Not financial advice.", "Not advice, just my read.", "NFA.", "Just data, not a call.",
    "Not financial advice, do your own research.", "Not a recommendation.",
]

MORNING_HEADS = [
    "☀️ Morning snapshot", "Morning check-in", "Good morning, crypto", "What the market did overnight",
    "Starting the day", "Morning numbers", "Overnight recap", "☀️ Morning",
]
EVENING_HEADS = [
    "🌙 Evening snapshot", "Evening check-in", "How the day ended", "Closing the day",
    "Evening numbers", "Day wrap", "🌙 Evening", "Where the market is tonight",
]


def pick(rng, items):
    return rng.choice(items)


def disclaimer(rng=None) -> str:
    return pick(rng or random, DISCLAIMERS)


def maybe(rng, p: float, text: str) -> str:
    return text if rng.random() < p else ""


def strip_emoji_prob(rng, text: str, p: float = 0.35) -> str:
    """С вероятностью p убирает ведущий эмодзи заголовка."""
    if rng.random() < p and text and not text[0].isalnum():
        return text.split(" ", 1)[-1]
    return text


def join_blocks(blocks: list[str], rng=None) -> str:
    """Склеивает непустые блоки; между ними иногда одна пустая строка, иногда
    без неё (там, где блок короткий) - рваный ритм как у ручных постов."""
    out = [b for b in blocks if b]
    return "\n\n".join(out)
