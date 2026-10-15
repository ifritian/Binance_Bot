"""
Формат "Вопрос аудитории" для Binance Square - без LLM.

Короткий вопрос с $CASHTAG известной монеты: легко ответить одной
строкой в комментариях, а комментарии - то, что Square поднимает в
ленте. Шаблоны написаны руками, тикер подставляется кодом из белого
списка (чтобы вопрос был про монету, которую люди знают).
"""
import random

import coin_whitelist
import config
import post_format

_COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "SUI", "TON", "LINK", "AVAX"]

TEMPLATES_EN = [
    "Where will ${t} be in a week: higher or lower than now? Drop your call below 👇",
    "${t} - are you holding, watching, or staying away? Tell us why 🤔",
    "If you had to pick one coin for the next month, would it be ${t}? Or something else?",
    "Which coin do you think is undervalued right now? My pick is ${t}. Yours?",
    "Would you buy ${t} on strength or wait for a pullback? 👇",
    "What is your main signal that ${t} is worth watching: volume, news, or the chart?",
]
TEMPLATES = [
    "Где окажется ${t} через неделю: выше или ниже, чем сейчас? Ваш вариант в комментариях 👇",
    "${t} — вы держите, наблюдаете или обходите стороной? Расскажите почему 🤔",
    "Если бы пришлось выбрать одну монету на месяц — это был бы ${t}? Или что-то другое?",
    "Какая монета, по-вашему, сейчас недооценена? Мой кандидат — ${t}. Ваш?",
    "${t} вы бы скорее купили на росте или дождались бы отката? 👇",
    "Что для вас главный сигнал, что ${t} стоит смотреть: объём, новости или график?",
]


def pick_question(last: str | None = None, rng: random.Random | None = None) -> tuple[str, str]:
    """Возвращает (текст для Square, тикер). Не повторяет прошлый текст подряд."""
    rng = rng or random
    coins = [c for c in _COINS if coin_whitelist.is_allowed(c)] or ["BTC"]
    for _ in range(10):
        ticker = rng.choice(coins)
        body = rng.choice(TEMPLATES_EN if config.SQUARE_TEMPLATE_LANG == "en" else TEMPLATES).replace("${t}", f"${ticker}")
        if body != last:
            break
    tags = post_format.square_hashtags_line(ticker)
    text = f"{body}\n\n{tags}" if tags else body
    return text, ticker
