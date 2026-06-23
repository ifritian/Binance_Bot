"""
Генератор поста "личное мнение" - публикуется раз в 2 дня (+- джиттер).

Тема ротируется между трёми вариантами, чтобы не быть всегда про BTC:
- "BTC" - движение цены BTC за 2 дня
- "ETH" - движение цены ETH за 2 дня
- "market" - средний % изменения по корзине топовых монет (BTC, ETH,
  SOL, BNB) - проще, чем тащить отдельный market-cap индекс, но даёт
  ощущение "рынок в целом", а не один актив

Во всех случаях % считаем сами по данным Binance (chart_generator.
fetch_klines), а не доверяем LLM придумывать цифры - LLM получает
готовое число и пишет вокруг него личную рефлексию.
"""
import logging
import random
from typing import Optional

import requests

import config
from chart_generator import fetch_klines
from post_format import DISCLAIMER, assemble_post

logger = logging.getLogger(__name__)

_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

THEMES: dict[str, dict] = {
    "BTC": {"label": "$BTC", "tickers": ["BTC"]},
    "ETH": {"label": "$ETH", "tickers": ["ETH"]},
    "market": {"label": "крипторынок в целом (по корзине BTC/ETH/SOL/BNB)", "tickers": ["BTC", "ETH", "SOL", "BNB"]},
}

_SYSTEM_PROMPT = """Ты пишешь короткий личный пост-мнение для Binance Square,
в разговорном фирменном стиле автора - не сухая аналитика, а живая
реакция человека, который следит за рынком. 2-4 предложения, можно
с риторическим вопросом, эмодзи (1-2), без воды.

Тебе дано ОДНО реальное число - % изменения за последние 2 дня по теме,
указанной в задании. Используй его ТОЧНО как дано, не округляй и не
придумывай других чисел. Это единственная цифра, которая должна быть
в тексте - не упоминай других активов или процентов сверх заданного.

НЕ добавляй сам никакой дисклеймер - это будет добавлено отдельно
после твоего текста.

Отвечай только текстом поста, без пояснений и без кавычек."""


def pick_theme(last_theme: Optional[str]) -> str:
    """Выбирает тему, отличную от последней использованной."""
    themes = list(THEMES.keys())
    if last_theme in themes and len(themes) > 1:
        themes = [t for t in themes if t != last_theme]
    return random.choice(themes)


def _calc_change_pct(ticker: str) -> Optional[float]:
    """% изменения цены тикера за последние 2 дня."""
    try:
        klines = fetch_klines(ticker, days=2)
    except requests.RequestException as e:
        logger.warning("Не удалось получить данные %s для поста-мнения: %s", ticker, e)
        return None

    if len(klines) < 2:
        return None

    open_price = float(klines[0][1])
    close_price = float(klines[-1][1])
    if open_price == 0:
        return None

    return (close_price - open_price) / open_price * 100


def _calc_theme_change_pct(theme: str) -> Optional[float]:
    """% изменения для выбранной темы - один тикер или среднее по корзине."""
    tickers = THEMES[theme]["tickers"]
    changes = [c for c in (_calc_change_pct(t) for t in tickers) if c is not None]

    if not changes:
        return None

    return round(sum(changes) / len(changes), 2)


def generate_opinion_post(theme: str) -> Optional[tuple[str, float]]:
    """Возвращает (готовый текст поста, % изменения), либо None, если
    не удалось получить данные."""
    pct = _calc_theme_change_pct(theme)
    if pct is None:
        return None

    sign = "+" if pct >= 0 else ""
    label = THEMES[theme]["label"]
    user_prompt = (
        f"Тема: {label}\n"
        f"Изменение за последние 2 дня: {sign}{pct}%.\n\n"
        f"Напиши личное мнение/наблюдение об этом движении рынка."
    )

    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.9,
        "max_tokens": 250,
    }
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}

    resp = requests.post(_GROQ_ENDPOINT, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    hook = data["choices"][0]["message"]["content"].strip()

    text = assemble_post(hook)
    logger.info("Сгенерирован пост-мнение (тема %s, %s%%): %s", theme, f"{sign}{pct}", text)
    return text, pct


def validate_opinion_post_text(text: str, expected_pct: float) -> tuple[bool, str]:
    """Проверяем, что в тексте есть именно то число, которое мы
    посчитали сами, и что дисклеймер на месте."""
    import re

    numbers = {float(n) for n in re.findall(r"[+-]?\d+\.?\d*", text)}
    if not any(abs(expected_pct - n) < 1e-6 for n in numbers):
        return False, f"В тексте не найден исходный %: {expected_pct}"

    if DISCLAIMER.lower() not in text.lower():
        return False, "В тексте отсутствует дисклеймер"

    return True, ""