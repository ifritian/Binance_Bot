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

import cliche_filter
import config
import requests
import voice_memory

from chart_generator import fetch_klines
from groq_client import call_groq
import post_format
from post_format import DISCLAIMER, HOOK_MODES, assemble_post
import voice_guidelines

logger = logging.getLogger(__name__)

THEMES: dict[str, dict] = {
    "BTC": {"label": "$BTC", "tickers": ["BTC"]},
    "ETH": {"label": "$ETH", "tickers": ["ETH"]},
    "SOL": {"label": "$SOL", "tickers": ["SOL"]},
    "BNB": {"label": "$BNB", "tickers": ["BNB"]},
    "XRP": {"label": "$XRP", "tickers": ["XRP"]},
    "ADA": {"label": "$ADA", "tickers": ["ADA"]},
    "DOGE": {"label": "$DOGE", "tickers": ["DOGE"]},
    "AVAX": {"label": "$AVAX", "tickers": ["AVAX"]},
    "LINK": {"label": "$LINK", "tickers": ["LINK"]},
    "DOT": {"label": "$DOT", "tickers": ["DOT"]},
    "market": {"label": "крипторынок в целом (по корзине BTC/ETH/SOL/BNB)", "label_en": "the crypto market as a whole (BTC/ETH/SOL/BNB basket)", "tickers": ["BTC", "ETH", "SOL", "BNB"]},
}


def _en() -> bool:
    return config.SQUARE_TEMPLATE_LANG != "ru"


def _label(theme: str) -> str:
    t = THEMES[theme]
    return t.get("label_en", t["label"]) if _en() else t["label"]


def _disclaimer() -> str:
    return post_format.DISCLAIMER_EN if _en() else DISCLAIMER


_SYSTEM_PROMPT_EN = """You write a personal market take for Binance Square in the voice of
someone who actually follows the market: a live reaction, not dry analysis.
3-5 sentences, at most one emoji, no filler, but give more context and
personal reflection than a bare statement of fact.

You are given a SET of real numbers (see the task). Use them EXACTLY as
given, never round or invent others. You may skip some, but you must NOT
mention numbers that are not in the set.

Structure (loose, not a template):
- a hook with one concrete number
- what it might mean / what it happened against (no invented news, only
  market reflection: "looks like", "not the first time")
- a personal take or one specific either/or question to the reader

Do NOT add any disclaimer, it is added separately after your text.

Reply with the post text only, no explanations, no quotes.""" + voice_guidelines.STYLE_DIRECTIVE_EN

_SYSTEM_PROMPT = """Ты пишешь личный пост-мнение для Binance Square, в
разговорном фирменном стиле автора - живая реакция человека, который
следит за рынком, а не сухая аналитика. 4-6 предложений, 
эмодзи (не больше одного), без воды - но за счёт длины дай
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
- личный вывод или конкретный вопрос к читателю с выбором из двух вариантов

НЕ добавляй сам никакой дисклеймер - это будет добавлено отдельно
после твоего текста.

Отвечай только текстом поста, без пояснений и без кавычек.""" + voice_guidelines.STYLE_DIRECTIVE


def pick_theme(last_theme: Optional[str]) -> str:
    """Выбирает тему, отличную от последней использованной."""
    themes = list(THEMES.keys())
    if last_theme in themes and len(themes) > 1:
        themes = [t for t in themes if t != last_theme]
    return random.choice(themes)


def calc_ticker_stats(ticker: str) -> Optional[dict]:
    """Реальные числа по тикеру за последние 2 дня: % изменения,
    амплитуда (high-low в % от открытия) и текущая цена. Всё считаем
    сами по тем же данным CoinGecko, без участия LLM.

    Публичная функция (без ведущего "_") - переиспользуется
    hot_take_generator.py для формата "Хот-тейк" (та же логика "дай LLM
    готовое реальное число, а не пусть придумывает").
    """
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


def calc_theme_stats(theme: str) -> Optional[dict]:
    """Для одного тикера (BTC/ETH) - полный набор (pct/амплитуда/цена).
    Для 'market' - % по каждому активу корзины + средний % по корзине
    (амплитуду и цену для разнородной корзины не считаем - бессмысленно
    усреднять цену BTC и SOL).

    Публичная функция - переиспользуется hot_take_generator.py."""
    tickers = THEMES[theme]["tickers"]

    if len(tickers) == 1:
        stats = calc_ticker_stats(tickers[0])
        if stats is None:
            return None
        return {"single": stats}

    breakdown = {}
    for t in tickers:
        stats = calc_ticker_stats(t)
        if stats is not None:
            breakdown[t] = stats["pct"]

    if not breakdown:
        return None

    avg_pct = round(sum(breakdown.values()) / len(breakdown), 2)
    return {"breakdown": breakdown, "avg_pct": avg_pct}


MIN_SINGLE_PCT = 2.0      # |движение за 48ч|, ниже которого "мнение" - пустая болтовня
MIN_SINGLE_SWING = 5.0    # ...если только амплитуда не большая
MIN_BASKET_AVG = 1.5
MIN_BASKET_ANY = 3.0

_STRUCTURES_EN = [
    "Open with the single most striking number, then one sentence on what it could mean.",
    "Open with a question to the reader, then give the number as context.",
    "Compare the move to what a calm market would look like, then give a clear lean (up/down/sideways) and say what would change your mind.",
    "Name one level or condition to watch next, and why.",
    "Write it as a short two-line note: what happened, what you are watching.",
]


def is_significant(stats: dict) -> bool:
    """Мнение пишем только когда есть что обсуждать - иначе получается
    '$XRP almost flat... 4% swing' ни о чём."""
    if "single" in stats:
        s = stats["single"]
        return abs(s["pct"]) >= MIN_SINGLE_PCT or s["amplitude_pct"] >= MIN_SINGLE_SWING
    vals = list(stats["breakdown"].values())
    return abs(stats["avg_pct"]) >= MIN_BASKET_AVG or any(abs(v) >= MIN_BASKET_ANY for v in vals)


def generate_opinion_post(theme: str, hook_mode: Optional[str] = None) -> Optional[tuple]:
    """Возвращает (готовый текст поста, набор разрешённых чисел для
    проверки), либо None, если не удалось получить данные.

    hook_mode - один из post_format.HOOK_MODES (тот же словарь "ярких
    авторских голосов", что используется для валютных сигналов в
    text_generator.py) - пост-мнение как раз то место, где выраженная
    личная интонация уместнее всего. Если None (например, старый код
    вызывает без параметра) - используется нейтральный системный промпт
    без персоны, как раньше."""
    stats = calc_theme_stats(theme)
    if stats is None:
        return None
    if not is_significant(stats):
        logger.info("Тема %s: движение слишком мелкое для мнения - пропускаю", theme)
        return None

    label = _label(theme)
    en = _en()
    system_prompt = _SYSTEM_PROMPT_EN if en else _SYSTEM_PROMPT
    if hook_mode is not None:
        system_prompt = f"{system_prompt}\n\n{post_format.hook_mode_text(hook_mode)}"

    if "single" in stats:
        s = stats["single"]
        sign = "+" if s["pct"] >= 0 else ""
        user_prompt = (
            f"Topic: {label}\n"
            f"Price change over the last 48 hours (2 days): {sign}{s['pct']}%.\n"
            f"Swing over that time (high-low as % of the starting price): {s['amplitude_pct']}%.\n"
            f"Current price: ${s['current_price']:.2f} (write it without thousands separators, as given).\n\n"
            f"If you mention the period, use EXACTLY '48 hours' or '2 days', do not convert it "
            f"(not '24 hours', not 'a day').\n\n"
            f"Write a personal take on this move."
        ) if en else (
            f"Тема: {label}\n"
            f"Изменение цены за последние 48 часов (2 дня): {sign}{s['pct']}%.\n"
            f"Амплитуда колебаний за это время (high-low в % от начальной цены): {s['amplitude_pct']}%.\n"
            f"Текущая цена: ${s['current_price']:.2f} (пиши без разделителей тысяч, как дано).\n\n"
            f"Если упоминаешь период времени в тексте - используй ТОЧНО '48 часов' или "
            f"'2 дня', не пересчитывай и не округляй по-своему (не '24 часа', не 'сутки').\n\n"
            f"Напиши личное мнение/наблюдение об этом движении рынка."
        )
        # 48/2 разрешены отдельно - промпт сам просит писать именно эти
        # числа для периода. abs(pct) разрешён отдельно от pct - если
        # движение отрицательное, LLM естественно пишет "снизился на
        # 2.11%" без явного минуса (слово "снизился" уже несёт смысл
        # падения), а без этого валидатор бpaковал бы честный текст.
        allowed_numbers = {s["pct"], abs(s["pct"]), s["amplitude_pct"], round(s["current_price"], 2), 48.0, 2.0}
        headline_pct = s["pct"]
    else:
        breakdown_lines = "\n".join(
            f"  ${t}: {'+' if pct >= 0 else ''}{pct}%" for t, pct in stats["breakdown"].items()
        )
        avg = stats["avg_pct"]
        user_prompt = (
            f"Topic: {label}\n"
            f"Change per asset over the last 48 hours (2 days):\n{breakdown_lines}\n"
            f"Average across the basket: {'+' if avg >= 0 else ''}{avg}%.\n\n"
            f"If you mention the period, use EXACTLY '48 hours' or '2 days', do not convert it "
            f"(not '24 hours', not 'a day').\n\n"
            f"Write a personal take on this move: you can mention single assets or the overall picture."
        ) if en else (
            f"Тема: {label}\n"
            f"Изменение по каждому активу за последние 48 часов (2 дня):\n{breakdown_lines}\n"
            f"Средний % по корзине: {'+' if avg >= 0 else ''}{avg}%.\n\n"
            f"Если упоминаешь период времени в тексте - используй ТОЧНО '48 часов' или "
            f"'2 дня', не пересчитывай и не округляй по-своему (не '24 часа', не 'сутки').\n\n"
            f"Напиши личное мнение/наблюдение об этом движении рынка - можно "
            f"упомянуть как отдельные активы, так и общую картину."
        )
        # См. комментарий в ветке "single" выше - те же два фикса
        # (48/2 разрешены отдельно, и модуль числа наравне со знаком).
        allowed_numbers = set(stats["breakdown"].values()) | {avg, 48.0, 2.0}
        allowed_numbers |= {abs(v) for v in stats["breakdown"].values()} | {abs(avg)}
        headline_pct = avg

    if en:
        user_prompt += "\n\nStructure for this post: " + random.choice(_STRUCTURES_EN)
    user_prompt += voice_memory.anti_repeat_block() + voice_memory.continuity_block(theme, label)

    hook = call_groq(system_prompt, user_prompt, max_tokens=500, temperature=0.9, model=config.GROQ_MODEL_SECONDARY)

    # Хук не должен быть пустым/почти пустым - без этой проверки
    # assemble_post() тихо собрал бы пост из одного дисклеймера, без
    # единой мысли автора (тот же баг, что был у index_signal/treasury/
    # accuracy_report/loss_review - см. их validate_*_hook). call_groq
    # уже перезапрашивает пустые ответы сам, это - подстраховка.
    if len(hook.strip()) < 10:
        logger.warning("Хук поста-мнения пустой или слишком короткий (%r) - публикую с нейтральным хуком", hook)
        # Запасной хук с реальным числом и конкретным вопросом (раньше тут был
        # пустой "как вам последнее движение?" - ровно то, что мы запретили).
        hook = (f"{label}: {headline_pct:+.2f}% in 2 days. Continuation or pullback?" if en
                else f"{label}: {headline_pct:+.2f}% за 2 дня. Продолжение или откат?")

    text = f"{hook.strip()}\n\n{_disclaimer()}"
    logger.info("Сгенерирован пост-мнение (тема %s, числа: %s): %s", theme, allowed_numbers, text)
    return text, allowed_numbers, headline_pct


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

    if _disclaimer().lower() not in text.lower():
        return False, "В тексте отсутствует дисклеймер"

    cliche_ok, found = cliche_filter.check_cliches(text)
    if not cliche_ok:
        return False, f"В тексте есть шаблонные ИИ-фразы: {', '.join(found)}"

    return True, ""