"""
Генератор статьи (формат Article в Binance Square) - публикуется раз
в неделю, подводит итог по дайджестам из канала за последние 7 дней.

Числа берутся из накопленной истории (queue_manager.get_digest_history),
которая заполняется при каждом увиденном дайджесте - LLM получает
готовый список фактов и оформляет их в текст статьи, не придумывая
собственных цифр.

Обложка статьи - график BTC за неделю, тот же chart_generator,
который используется для постов про конкретные валюты.
"""
import logging
import re
from pathlib import Path
from typing import Optional

import requests

import config
from chart_generator import generate_chart_image
from post_format import DISCLAIMER, assemble_post

logger = logging.getLogger(__name__)

_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
_WEEK_SECONDS = 7 * 24 * 3600

_SYSTEM_PROMPT = """Ты пишешь еженедельную статью-сводку для Binance Square
в фирменном стиле автора - живо, без канцелярита, но информативно.
Статья подводит итог по сигналам за неделю: что сработало, что не
оправдало ожиданий, общая картина.

Тебе дан список фактов (тикер, % изменения, score, результат) - вставляй
эти числа ТОЧНО как даны, без округления и без придумывания новых чисел
сверх того, что в списке.

Структура статьи:
1. Короткое вступление (1-2 предложения, можно с эмодзи)
2. Краткий разбор 3-5 самых заметных сигналов недели с их % и score
3. Итоговый вывод/мысль

НЕ добавляй сам дисклеймер - он будет добавлен отдельно после текста.

Отвечай только текстом статьи (без заголовка - заголовок придумаешь
отдельно), без пояснений и без кавычек."""

_TITLE_PROMPT = """Придумай короткий цепляющий заголовок для статьи-сводки
по итогам недели в крипте (на основе тех же фактов). Без кавычек,
одна строка, не длиннее 70 символов."""

# Дополнительный акцент в зависимости от того, как сложилась неделя -
# добавляется к базовому промпту, чтобы статья не звучала одинаково
# независимо от того, что реально произошло.
_COMPOSITION_EMPHASIS = {
    "mostly_wins": (
        "Эта неделя была в основном удачной (большинство сигналов сработали "
        "в плюс) - сделай акцент на том, что сработало и почему, без "
        "избыточного праздничного тона, но дай это прочувствовать."
    ),
    "mostly_losses": (
        "Эта неделя была сложной (большинство сигналов не оправдали "
        "ожиданий) - не превращай это в извинения, но честно отметь это, "
        "и сделай акцент на выводах и важности риск-менеджмента, а не "
        "только на перечислении фактов."
    ),
    "mixed": (
        "Неделя получилась смешанной (примерно поровну удачных и неудачных "
        "сигналов) - дай сбалансированный разбор, не перекашивая в сторону "
        "только успехов или только неудач."
    ),
}


def _analyze_week_composition(history: list[dict]) -> str:
    """Определяет, как сложилась неделя: 'mostly_wins' (преимущественно
    лонг-сигналы), 'mostly_losses' (преимущественно шорт-сигналы) или
    'mixed' - по доле сигналов с направлением 'лонг' в истории."""
    if not history:
        return "mixed"

    long_count = sum(1 for h in history if "лонг" in h.get("direction", "").lower())
    ratio = long_count / len(history)

    if ratio >= 0.65:
        return "mostly_wins"
    if ratio <= 0.35:
        return "mostly_losses"
    return "mixed"


def _format_facts(history: list[dict]) -> str:
    lines = []
    for h in history:
        lines.append(
            f"- ${h['ticker']} | {h['timeframe']} | {h.get('direction', '')} | "
            f"{h.get('strategy', '')} | {h['change_pct']} | score {h['score']}"
        )
    return "\n".join(lines)


def _call_groq(system_prompt: str, user_prompt: str, max_tokens: int = 600) -> str:
    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.8,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}
    resp = requests.post(_GROQ_ENDPOINT, json=payload, headers=headers, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def generate_weekly_article(history: list[dict]) -> Optional[tuple[str, str, list[dict]]]:
    """
    Возвращает (title, body_text, history) или None, если истории
    недостаточно для статьи (например, бот только что запущен и
    данных за неделю ещё не накопилось).
    """
    if len(history) < 3:
        logger.info("Недостаточно данных для статьи (%d записей за неделю) - пропускаю", len(history))
        return None

    facts = _format_facts(history)
    composition = _analyze_week_composition(history)
    system_prompt = f"{_SYSTEM_PROMPT}\n\n{_COMPOSITION_EMPHASIS[composition]}"

    body_hook = _call_groq(system_prompt, f"Факты за неделю:\n{facts}")
    title = _call_groq(_TITLE_PROMPT, f"Факты за неделю:\n{facts}", max_tokens=50)
    title = title.strip().strip('"').strip("«»")[:70]

    body = assemble_post(body_hook)
    logger.info(
        "Сгенерирована статья: %s (фактов: %d, композиция недели: %s)",
        title, len(history), composition,
    )
    return title, body, history


def generate_cover_image() -> Optional[Path]:
    """Обложка статьи - график BTC за последнюю неделю."""
    return generate_chart_image("BTC", days=7)


def validate_article_text(title: str, body: str, history: list[dict]) -> tuple[bool, str]:
    """
    Проверяем, что числа в статье соответствуют истории - то есть
    каждое число, упомянутое в тексте, должно встречаться хотя бы
    в одном из фактов истории (защита от придуманных LLM цифр).
    """
    if not title.strip():
        return False, "Пустой заголовок статьи"

    if DISCLAIMER.lower() not in body.lower():
        return False, "В статье отсутствует дисклеймер"

    # Все числа, которые разрешены - score и % из истории
    allowed_numbers = set()
    for h in history:
        allowed_numbers.add(float(h["score"]))
        try:
            allowed_numbers.add(float(h["change_pct"].rstrip("%")))
        except ValueError:
            pass

    found_numbers = {float(n) for n in re.findall(r"[+-]?\d+\.?\d*", body)}
    unknown = [n for n in found_numbers if not any(abs(n - a) < 1e-6 for a in allowed_numbers)]

    if unknown:
        return False, f"В статье есть числа, не из истории дайджестов: {unknown}"

    return True, ""