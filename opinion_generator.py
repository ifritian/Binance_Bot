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

_SYSTEM_PROMPT = """Ты пишешь личный пост-мнение для Binance Square, в
разговорном фирменном стиле автора - живая реакция человека, который
следит за рынком, а не сухая аналитика. 4-6 предложений, можно с
риторическим вопросом, эмодзи (1-2), без воды - но за счёт длины дай
больше контекста и личной рефлексии, чем просто констатация факта.

Тебе дан НАБОР реальных чисел (см. задание) - используй их ТОЧНО как
дано, не округляй и не придумывай других чисел. Можно использовать не
все числа из набора, если не нужно для текста, но НЕЛЬЗЯ упоминать
числа, которых там нет.

Структура (свободно, не как шаблон):
- зацепка с конкретной цифрой
- что это может значить / на фоне чего это произошло (без выдумывания
  новостей - просто рыночная рефлексия, "похоже на...", "не первый раз
  когда...")
- лёгкий вопрос к читателю или личный вывод

НЕ добавляй сам никакой дисклеймер - это будет добавлено отдельно
после твоего текста.

Отвечай только текстом поста, без пояснений и без кавычек."""


def pick_theme(last_theme: Optional[str]) -> str:
    """Выбирает тему, отличную от последней использованной."""
    themes = list(THEMES.keys())
    if last_theme in themes and len(themes) > 1:
        themes = [t for t in themes if t != last_theme]
    return random.choice(themes)


def _calc_ticker_stats(ticker: str) -> Optional[dict]:
    """Реальные числа по тикеру за последние 2 дня: % изменения,
    амплитуда (high-low в % от открытия) и текущая цена. Всё считаем
    сами по тем же данным CoinGecko, без участия LLM."""
    try:
        klines = fetch_klines(ticker, days=2)
    except requests.RequestException as e:
        logger.warning("Не удалось получить данные %s для поста-мнения: %s", ticker, e)
        return None

    if len(klines) < 2:
        return None

    opens = [float(k["open"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    closes = [float(k["close"]) for k in klines]

    open_price, close_price = opens[0], closes[-1]
    if open_price == 0:
        return None

    pct = round((close_price - open_price) / open_price * 100, 2)
    amplitude_pct = round((max(highs) - min(lows)) / open_price * 100, 2)
    return {"pct": pct, "amplitude_pct": amplitude_pct, "current_price": close_price}


def _calc_theme_stats(theme: str) -> Optional[dict]:
    """Для одного тикера (BTC/ETH) - полный набор (pct/амплитуда/цена).
    Для 'market' - % по каждому активу корзины + средний % по корзине
    (амплитуду и цену для разнородной корзины не считаем - бессмысленно
    усреднять цену BTC и SOL)."""
    tickers = THEMES[theme]["tickers"]

    if len(tickers) == 1:
        stats = _calc_ticker_stats(tickers[0])
        if stats is None:
            return None
        return {"single": stats}

    breakdown = {}
    for t in tickers:
        stats = _calc_ticker_stats(t)
        if stats is not None:
            breakdown[t] = stats["pct"]

    if not breakdown:
        return None

    avg_pct = round(sum(breakdown.values()) / len(breakdown), 2)
    return {"breakdown": breakdown, "avg_pct": avg_pct}


def generate_opinion_post(theme: str) -> Optional[tuple[str, set[float]]]:
    """Возвращает (готовый текст поста, набор разрешённых чисел для
    проверки), либо None, если не удалось получить данные."""
    stats = _calc_theme_stats(theme)
    if stats is None:
        return None

    label = THEMES[theme]["label"]

    if "single" in stats:
        s = stats["single"]
        sign = "+" if s["pct"] >= 0 else ""
        user_prompt = (
            f"Тема: {label}\n"
            f"Изменение цены за последние 2 дня: {sign}{s['pct']}%.\n"
            f"Амплитуда колебаний за это время (high-low в % от начальной цены): {s['amplitude_pct']}%.\n"
            f"Текущая цена: ${s['current_price']:.2f} (пиши без разделителей тысяч, как дано).\n\n"
            f"Напиши личное мнение/наблюдение об этом движении рынка."
        )
        allowed_numbers = {s["pct"], s["amplitude_pct"], round(s["current_price"], 2)}
    else:
        breakdown_lines = "\n".join(
            f"  ${t}: {'+' if pct >= 0 else ''}{pct}%" for t, pct in stats["breakdown"].items()
        )
        avg = stats["avg_pct"]
        user_prompt = (
            f"Тема: {label}\n"
            f"Изменение по каждому активу за последние 2 дня:\n{breakdown_lines}\n"
            f"Средний % по корзине: {'+' if avg >= 0 else ''}{avg}%.\n\n"
            f"Напиши личное мнение/наблюдение об этом движении рынка - можно "
            f"упомянуть как отдельные активы, так и общую картину."
        )
        allowed_numbers = set(stats["breakdown"].values()) | {avg}

    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.9,
        "max_tokens": 400,
    }
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}

    resp = requests.post(_GROQ_ENDPOINT, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    hook = data["choices"][0]["message"]["content"].strip()

    text = assemble_post(hook)
    logger.info("Сгенерирован пост-мнение (тема %s, числа: %s): %s", theme, allowed_numbers, text)
    return text, allowed_numbers


def validate_opinion_post_text(text: str, allowed_numbers: set[float]) -> tuple[bool, str]:
    """Проверяем, что числа в тексте - подмножество тех, что мы сами
    посчитали (allowed_numbers), и что дисклеймер на месте. Текст не
    обязан использовать ВСЕ числа из набора, но не может содержать
    числа, которых там нет."""
    import re

    numbers = {float(n) for n in re.findall(r"[+-]?\d+\.?\d*", text.replace(",", ""))}
    unknown = [n for n in numbers if not any(abs(n - a) < 0.05 for a in allowed_numbers)]
    if unknown:
        return False, f"В тексте есть числа, не из посчитанных данных: {unknown}"

    if DISCLAIMER.lower() not in text.lower():
        return False, "В тексте отсутствует дисклеймер"

    return True, ""