"""
Генератор поста "личное мнение" - публикуется раз в 2 дня.

В отличие от постов про конкретную валюту (из канала), здесь нет
внешнего источника-события. По договорённости источник - реальное
движение цены BTC за последние 2 дня, как самый репрезентативный
индикатор настроения рынка. % изменения считаем сами по данным
Binance (chart_generator.fetch_klines), а не доверяем LLM придумывать
цифры - LLM получает готовое число и пишет вокруг него личную
рефлексию, а не аналитику с цифрами по другим активам.
"""
import logging

import requests

import config
from chart_generator import fetch_klines
from post_format import assemble_post

logger = logging.getLogger(__name__)

_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
_PERIOD_HOURS = 48  # 2 дня

_SYSTEM_PROMPT = """Ты пишешь короткий личный пост-мнение для Binance Square,
в разговорном фирменном стиле автора - не сухая аналитика, а живая
реакция человека, который следит за рынком. 2-4 предложения, можно
с риторическим вопросом, эмодзи (1-2), без воды.

Тебе дано ОДНО реальное число - изменение цены BTC за последние 2 дня.
Используй его ТОЧНО как дано, не округляй и не придумывай других чисел.
Это единственная цифра, которая должна быть в тексте - не упоминай
других активов, других процентов или уровней.

НЕ добавляй сам никакой дисклеймер - это будет добавлено отдельно
после твоего текста.

Отвечай только текстом поста, без пояснений и без кавычек."""


def _calc_btc_change_pct() -> float | None:
    """% изменения цены BTC за последние 48 часов, посчитанный нами
    самостоятельно по реальным данным - не отдаётся на волю LLM."""
    try:
        klines = fetch_klines("BTC", interval="4h", limit=12)  # 12 * 4ч = 48ч
    except requests.RequestException as e:
        logger.warning("Не удалось получить данные BTC для поста-мнения: %s", e)
        return None

    if len(klines) < 2:
        return None

    open_price = float(klines[0][1])
    close_price = float(klines[-1][4])
    if open_price == 0:
        return None

    return round((close_price - open_price) / open_price * 100, 2)


def generate_opinion_post() -> tuple[str, float] | None:
    """Возвращает (готовый текст поста, % BTC), либо None, если не
    удалось получить данные BTC."""
    pct = _calc_btc_change_pct()
    if pct is None:
        return None

    sign = "+" if pct >= 0 else ""
    user_prompt = (
        f"Изменение цены BTC за последние 2 дня: {sign}{pct}%.\n\n"
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
    logger.info("Сгенерирован пост-мнение (BTC %s%%): %s", f"{sign}{pct}", text)
    return text, pct


def validate_opinion_post_text(text: str, expected_pct: float) -> tuple[bool, str]:
    """Проверяем, что в тексте есть именно то число BTC %, которое
    мы посчитали сами, и что дисклеймер на месте."""
    import re

    from post_format import DISCLAIMER

    numbers = {float(n) for n in re.findall(r"[+-]?\d+\.?\d*", text)}
    if not any(abs(expected_pct - n) < 1e-6 for n in numbers):
        return False, f"В тексте не найден исходный % BTC: {expected_pct}"

    if DISCLAIMER.lower() not in text.lower():
        return False, "В тексте отсутствует дисклеймер"

    return True, ""