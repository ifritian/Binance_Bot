"""
Формат "Вопрос аудитории" для Binance Square - без LLM.

Короткий вопрос с $CASHTAG известной монеты: легко ответить одной
строкой в комментариях, а комментарии - то, что Square поднимает в
ленте. Шаблоны написаны руками, тикер подставляется кодом из белого
списка (чтобы вопрос был про монету, которую люди знают).
"""
import logging
import random

import requests

import coin_whitelist
import config
import post_format

logger = logging.getLogger(__name__)

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
    "${t} - вы держите, наблюдаете или обходите стороной? Расскажите почему 🤔",
    "Если бы пришлось выбрать одну монету на месяц - это был бы ${t}? Или что-то другое?",
    "Какая монета, по-вашему, сейчас недооценена? Мой кандидат - ${t}. Ваш?",
    "${t} вы бы скорее купили на росте или дождались бы отката? 👇",
    "Что для вас главный сигнал, что ${t} стоит смотреть: объём, новости или график?",
]


# Вопросы про реальное движение монеты за сутки: актуальнее и точнее задают
# повод нажать на тикер, чем универсальные шаблоны (те остаются запасными).
# {abs} - модуль без знака (после слов up/down/lost), {pct} - со знаком.
_UP_EN = [
    "${t} is up {abs} in 24h. Chasing it or waiting for a pullback? 👇",
    "${t} {pct} today. Late to the move, or is there more room? 🤔",
    "${t} is climbing ({pct} in 24h). Would you buy here or wait?",
]
_DOWN_EN = [
    "${t} is down {abs} in 24h. A dip to buy or a drop to avoid? 👇",
    "${t} {pct} today. Are you buying this, or waiting for the bottom? 🤔",
    "${t} just lost {abs}. Where would you expect buyers to step in?",
]
_FLAT_EN = [
    "${t} barely moved today ({pct}). Quiet before a move, or just a dull day? 🤔",
    "${t} is flat ({pct} in 24h). Which way does it break: up or down? 👇",
]
_UP_RU = [
    "${t} вырос на {abs} за сутки. Догоняете или ждёте отката? 👇",
    "${t} {pct} за сегодня. Поезд ушёл или запас хода есть? 🤔",
]
_DOWN_RU = [
    "${t} потерял {abs} за сутки. Покупать просадку или держаться в стороне? 👇",
    "${t} {pct} за сегодня. Ловите отскок или ждёте дна? 🤔",
]
_FLAT_RU = [
    "${t} почти не двигается ({pct} за сутки). Затишье перед движением или скучный день? 🤔",
]


def _fetch_changes(coins: list[str]) -> dict[str, float]:
    """{тикер: изменение за 24ч в %}; пусто при любой ошибке сети."""
    import json
    import scanner
    try:
        symbols = json.dumps([f"{c}USDT" for c in coins], separators=(",", ":"))
        resp = requests.get(f"{scanner._BASE_URL}/ticker/24hr", params={"symbols": symbols}, timeout=15)
        resp.raise_for_status()
        return {r["symbol"].removesuffix("USDT"): float(r["priceChangePercent"]) for r in resp.json()}
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.warning("Вопрос аудитории: нет данных о движении монет: %s", e)
        return {}


def pick_data_question(last: str | None = None, rng: random.Random | None = None,
                       changes: dict[str, float] | None = None) -> tuple[str, str] | None:
    """Вопрос про реальное движение: из самых заметных за сутки монет случайная,
    текст зависит от направления. None, если данных нет (тогда шаблон)."""
    rng = rng or random
    coins = [c for c in _COINS if coin_whitelist.is_allowed(c)] or ["BTC"]
    changes = _fetch_changes(coins) if changes is None else changes
    ranked = sorted(((abs(p), c, p) for c, p in changes.items() if c in coins), reverse=True)
    if not ranked:
        return None
    en = config.SQUARE_TEMPLATE_LANG == "en"
    for _ in range(10):
        _a, ticker, pct = rng.choice(ranked[:4])
        if pct >= 2.0:
            pool = _UP_EN if en else _UP_RU
        elif pct <= -2.0:
            pool = _DOWN_EN if en else _DOWN_RU
        else:
            pool = _FLAT_EN if en else _FLAT_RU
        signed = f"{pct:+.1f}%".replace("-", "−")
        body = (rng.choice(pool).replace("${t}", f"${ticker}")
                .replace("{pct}", signed).replace("{abs}", f"{abs(pct):.1f}%"))
        if body.split("\n\n")[0] != last:
            break
    tags = post_format.square_hashtags_line(ticker)
    return (f"{body}\n\n{tags}" if tags else body), ticker


def pick_question(last: str | None = None, rng: random.Random | None = None) -> tuple[str, str]:
    """Возвращает (текст для Square, тикер). Не повторяет прошлый текст подряд.
    Сначала вопрос по реальному движению монеты, при отсутствии данных - шаблон."""
    rng = rng or random
    data_q = pick_data_question(last, rng)
    if data_q is not None:
        return data_q
    coins = [c for c in _COINS if coin_whitelist.is_allowed(c)] or ["BTC"]
    for _ in range(10):
        ticker = rng.choice(coins)
        body = rng.choice(TEMPLATES_EN if config.SQUARE_TEMPLATE_LANG == "en" else TEMPLATES).replace("${t}", f"${ticker}")
        if body != last:
            break
    tags = post_format.square_hashtags_line(ticker)
    text = f"{body}\n\n{tags}" if tags else body
    return text, ticker
