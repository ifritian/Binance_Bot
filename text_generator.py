"""
Генерация текста поста на основе дайджеста follow-up результатов
или качественного инсайта по картинке.

Структура поста зафиксирована программно, а не оставлена на волю LLM:
1. Короткий хук (1-3 предложения, генерирует LLM, тон ротируется -
   см. post_format.HOOK_MODES)
2. Пустая строка-разделитель
3. Дисклеймер - фиксированная фраза, добавляется кодом ниже, а не
   LLM, чтобы формулировка была гарантированно точной в каждом посте.

В отличие от прежнего формата сигналов (вход/цель/стоп), здесь LLM
не публикует торговый призыв "входи здесь" - это разбор того, как
отработал предыдущий сигнал (% движения, score). Числа, которые
нельзя искажать - это % изменения и score, не цена.

Groq отдаёт OpenAI-совместимый /chat/completions, используем обычный
requests без специального SDK.
"""
import logging

import requests

import config
from image_analyzer import ImageInsight
from post_format import HOOK_MODES, assemble_post
from signal_parser import FollowUpEntry

logger = logging.getLogger(__name__)

_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

# Примеры твоих прошлых постов - few-shot, чтобы LLM держал стиль:
# короткая мысль-хук, cashtag, разговорный тон.
_STYLE_EXAMPLES = """
Пример 1:
$ARB RSI below 30, I think there will be a rollback, but will the currency continue to fall further, this is the main question 🤔

Пример 2:
$TRUMP little by little it grows, I wonder how far it will go🤔
#TRUMP
"""

_BASE_SYSTEM_PROMPT = f"""Ты пишешь короткий ХУК для поста на Binance Square в фирменном стиле автора.
Стиль: 1-3 коротких предложения, тикер как $CASHTAG в начале, разговорный тон,
уместный эмодзи (не более 1-2). Без воды.

Примеры стиля автора:
{_STYLE_EXAMPLES}

Контекст постов: это разбор того, как отработал предыдущий сигнал
(не призыв входить в позицию сейчас). Передай суть - актив двигался
в ожидаемом направлении и насколько именно.

КРИТИЧЕСКИ ВАЖНОЕ ПРАВИЛО: если в задании указаны конкретные числа
(% изменения, score) - вставляй их в текст ТОЧНО как есть, без
округления и без изменений. Не придумывай и не пересчитывай числа.
Не упоминай чисел, которых не было в задании.

НЕ добавляй сам никакой дисклеймер и никакие фразы про "не финансовая
рекомендация" - это будет добавлено отдельно после твоего текста.

Отвечай только текстом хука, без пояснений и без кавычек вокруг текста."""

_BASE_IMAGE_SYSTEM_PROMPT = f"""Ты пишешь короткий ХУК для поста на Binance Square в фирменном стиле автора.
Стиль: 1-3 коротких предложения, тикер как $CASHTAG в начале, разговорный тон,
уместный эмодзи (не более 1-2). Без воды.

Примеры стиля автора:
{_STYLE_EXAMPLES}

Контекст: тебе дано только качественное наблюдение по графику (без
конкретных цифр - они ненадёжны при распознавании со скриншота).
Пиши в духе "интересно, как далеко пойдёт" / "стоит понаблюдать" -
качественно, без точных уровней входа/цели/процентов.

КРИТИЧЕСКИ ВАЖНОЕ ПРАВИЛО: НЕ упоминай никаких чисел, процентов,
цен или конкретных уровней - их нет в исходных данных, придумывать
их запрещено.

НЕ добавляй сам никакой дисклеймер и никакие фразы про "не финансовая
рекомендация" - это будет добавлено отдельно после твоего текста.

Отвечай только текстом хука, без пояснений и без кавычек вокруг текста."""


def _call_groq(system_prompt: str, user_prompt: str) -> str:
    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 300,
    }
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}

    resp = requests.post(_GROQ_ENDPOINT, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def generate_post_text(entry: FollowUpEntry, digest_title: str, hook_mode: str) -> str:
    result_ru = "сработал в плюс" if entry.result == "favorable" else "не оправдал ожиданий"
    system_prompt = f"{_BASE_SYSTEM_PROMPT}\n\n{HOOK_MODES[hook_mode]}"

    user_prompt = f"""Заголовок дайджеста: {digest_title}
Тикер: ${entry.ticker}
Таймфрейм отслеживания: {entry.timeframe}
Результат: {result_ru}
Изменение: {entry.change_pct}
Score: {entry.score}

Напиши хук в стиле автора, обязательно включив изменение в % и score
ровно такими, как указаны выше."""

    hook = _call_groq(system_prompt, user_prompt)
    text = assemble_post(hook)
    logger.info("Сгенерирован текст поста для %s (режим %s): %s", entry.ticker, hook_mode, text)
    return text


def generate_post_text_from_image(insight: ImageInsight, hook_mode: str) -> str:
    direction_ru = {
        "up": "движение вверх",
        "down": "движение вниз",
        "unclear": "направление не очевидно",
    }.get(insight.direction, "направление не очевидно")
    system_prompt = f"{_BASE_IMAGE_SYSTEM_PROMPT}\n\n{HOOK_MODES[hook_mode]}"

    user_prompt = f"""Тикер: ${insight.ticker}
Направление: {direction_ru}
Наблюдение: {insight.note}

Напиши хук в стиле автора. Никаких чисел и процентов - только
качественное наблюдение."""

    hook = _call_groq(system_prompt, user_prompt)
    text = assemble_post(hook)
    logger.info("Сгенерирован текст поста по картинке для %s (режим %s): %s", insight.ticker, hook_mode, text)
    return text