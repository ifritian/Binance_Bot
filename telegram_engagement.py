"""
telegram_engagement.py - формат "Опросы/AMA" (Telegram, Этап 3).

Два формата живого общения в канале, оба используют то, что даёт именно
Telegram Bot API (в отличие от Bluesky, где "вопрос аудитории" - просто
текст, потому что нативных опросов там нет):

1. POLLS - нативные Telegram-опросы (sendPoll, см. telegram_publisher.
   publish_poll) с закрытыми вариантами ответа - голосование даёт
   мгновенный агрегированный результат, что для канала более "живое",
   чем открытый вопрос в комментах.
2. AMA_PROMPTS - приглашение задать вопросы в комментариях, на которые
   разбор выйдет отдельным постом позже (сам разбор ответов - за
   пределами автоматизации, это делает человек).

Оба пула - статические, без LLM (как и audience_question_generator.py
для Bluesky) - ни голосование, ни приглашение на AMA не требуют
генерации текста, а любой LLM-вызов тут был бы лишним риском без
пользы.
"""
import logging
import random
from typing import Optional

import config

logger = logging.getLogger(__name__)

POLLS_RU: list[dict] = [
    {
        "question": "Какую тему разобрать в следующем посте глоссария?",
        "options": ["RSI и дивергенция", "Риск и размер позиции", "Индекс и ребалансировка", "Своё - в комменты"],
    },
    {
        "question": "Как вам последняя неделя сигналов?",
        "options": ["Огонь 🔥", "Нормально", "Не очень", "Давно не проверял(а)"],
    },
    {
        "question": "Что чаще торгуете?",
        "options": ["Спот", "Фьючерсы", "И то, и другое", "Пока только слежу"],
    },
    {
        "question": "Сколько сигналов в день - ваш комфортный объём?",
        "options": ["Чем больше, тем лучше", "2-3 в день", "1 в день, но точный", "Реже, но избранные"],
    },
    {
        "question": "Какой формат разбора для вас полезнее?",
        "options": ["Короткий сигнал", "Разбор с 'Контекстом'", "И то, и другое"],
    },
    {
        "question": "На каком таймфрейме вы обычно смотрите рынок?",
        "options": ["15м", "1ч", "1д", "Смотрю несколько сразу"],
    },
    {
        "question": "Что для вас важнее в сигнале?",
        "options": ["Точность входа", "Соотношение риск/прибыль", "Оба фактора одинаково"],
    },
    {
        "question": "Читаете ли вы блок 'Контекст' в сигналах?",
        "options": ["Да, всегда", "Иногда", "Обычно нет, сразу к цифрам"],
    },
]

AMA_PROMPTS_RU: list[str] = [
    "🎤 Открываю АМА - задавайте любые вопросы про сигналы, стратегию или индекс в комментариях, отвечу разбором в одном из следующих постов.",
    "🎤 Накопились вопросы про то, как читать сигналы, откуда цифры или почему индекс устроен именно так? Пишите в комменты - соберу самые частые в отдельный разбор.",
    "🎤 Раз в какое-то время стоит остановиться и спросить: что вам непонятно или интересно узнать подробнее? Задавайте вопросы в комментариях.",
]


POLLS_EN: list[dict] = [
    {"question": "What do you trade most?", "options": ["Spot", "Futures", "Both", "Just watching"]},
    {"question": "Where does BTC go this week?", "options": ["Up", "Sideways", "Down", "No idea"]},
    {"question": "Which post is most useful to you?",
     "options": ["Market snapshot", "Binance announcements", "Trend ideas", "News + price check"]},
    {"question": "How much of your portfolio is in altcoins?", "options": ["Under 10%", "10-30%", "30-60%", "Over 60%"]},
    {"question": "Do you use stop-losses?", "options": ["Always", "Sometimes", "Never"]},
    {"question": "What timeframe do you usually watch?", "options": ["15m", "1h", "4h", "1d"]},
    {"question": "When the Fear & Greed Index shows extreme fear, you usually...",
     "options": ["Buy", "Wait", "Sell", "Ignore the index"]},
    {"question": "What should we cover more?",
     "options": ["Binance events and delistings", "Altcoin basket vs BTC", "Trend ideas", "Coin news"]},
]

AMA_PROMPTS_EN: list[str] = [
    "🎤 Questions about how the ideas are built, where the numbers come from, or how to read a post? Ask in the comments and the most common ones get a full answer.",
    "🎤 Anything unclear in the market snapshots or the Binance Radar? Write it below and we will explain it in a follow-up post.",
    "🎤 What would you like to see more of in this channel? Tell us in the comments.",
]

POLLS = POLLS_EN if config.SQUARE_TEMPLATE_LANG != "ru" else POLLS_RU
AMA_PROMPTS = AMA_PROMPTS_EN if config.SQUARE_TEMPLATE_LANG != "ru" else AMA_PROMPTS_RU


def pick_poll(last_question: Optional[str]) -> dict:
    """Выбирает опрос, отличный от последнего заданного (сравнение по
    тексту вопроса - тот же принцип ротации, что и в
    audience_question_generator.pick_question)."""
    pool = POLLS
    if last_question in {p["question"] for p in pool} and len(pool) > 1:
        pool = [p for p in pool if p["question"] != last_question]
    return random.choice(pool)


def pick_ama_prompt(last_prompt: Optional[str]) -> str:
    """Выбирает приглашение на AMA, отличное от последнего использованного."""
    pool = AMA_PROMPTS
    if last_prompt in pool and len(pool) > 1:
        pool = [p for p in pool if p != last_prompt]
    return random.choice(pool)
