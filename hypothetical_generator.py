"""
hypothetical_generator.py - формат "А что если?" (ТОЛЬКО Binance Square).

По запросу пользователя (референс - скриншот чужого поста на Square с
гипотетическим сравнением тикеров) - шуточный/развлекательный пост:
описывается ИЗ РЯДА ВОН ВЫХОДЯЩАЯ, нарочито гипотетическая ситуация про
один тикер + короткий тезис/предположение, что бы это могло значить.
Явно НЕ прогноз и не сигнал - мысленный эксперимент, поэтому:

- ЧИСЕЛ В ТЕКСТЕ НЕТ ВООБЩЕ (как и у binance_promo_generator - тот же
  принцип: если нет реальных данных, которые можно дать LLM как факт,
  проще запретить числа целиком, чем потом ловить выдумку валидатором
  постфактум - см. article_generator.py, тот баг с ценой "$140,000",
  которого мы больше не хотим повторять в новом формате);
- пост должен явно читаться как игра воображения - validate_hypothetical_post
  требует хотя бы одну "рамочную" фразу (см. _FRAMING_MARKERS) вроде
  "что если" / "представь" - без неё гипотетический сценарий рискует
  прочитаться как реальное заявление или прогноз.

Картинка - обычный график тикера (chart_generator.generate_chart_image,
БЕЗ annotation - это не про конкретную сделку) - генерируется в
main.try_publish_hypothetical_post, тем же паттерном "неудача графика не
блокирует публикацию", что и у остальных форматов с картинкой.

ЧАСТОТА: редко, тем же паттерном, что и news_opinion_generator - не
чаще раза в MIN_DAYS_BETWEEN_HYPOTHETICAL_POSTS дней. Публикуется ТОЛЬКО
на Binance Square (см. main.try_publish_hypothetical_post) - это
площадочно-развлекательный формат, не про рыночный сигнал, кросспостить
в Telegram/Bluesky смысла нет (как и у binance_promo).

Ротация тикера - через собственный queue_manager.get_last_hypothetical_ticker/
set_last_hypothetical_ticker (отдельно от opinion/hot_take/promo, чтобы
темы этих форматов не были всегда синхронны).
"""
import logging
import random
import re
from typing import Optional

import cliche_filter
import validator
from groq_client import call_groq
from post_format import DISCLAIMER, HOOK_MODES, assemble_post
import voice_guidelines
import voice_memory

logger = logging.getLogger(__name__)

MIN_DAYS_BETWEEN_HYPOTHETICAL_POSTS = 3  # тот же порядок частоты, что и у новостного формата

# Узнаваемые крупные тикеры - у всех надёжно есть данные для графика
# (chart_generator.fetch_klines), и аудитория сразу считывает тему без
# пояснений - для шуточного формата это важнее, чем разнообразие.
_TICKERS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"]

# Хотя бы одна из этих фраз (без учёта регистра) должна быть в тексте -
# явный сигнал читателю, что дальше игра воображения, а не реальное
# заявление о рынке. Без такой рамки шуточный сценарий может прочитаться
# как настоящая новость/прогноз.
_FRAMING_MARKERS = (
    "что если", "а что если", "представь", "представим", "вообрази",
    "мысленный эксперимент", "прикинь", "на секунду представь",
)

_SYSTEM_PROMPT = """Ты пишешь короткий шуточный "а что если?" пост для
Binance Square про $TICKER. Формат - мысленный эксперимент: опиши ИЗ РЯДА
ВОН ВЫХОДЯЩУЮ, нарочито гипотетическую (может быть абсурдной или просто
неожиданной) ситуацию с этой монетой, и добавь короткий тезис или
предположение - что бы это могло значить, если бы вдруг произошло.
2-4 предложения всего.

Обязательно открой пост (или явно обозначь внутри) фразой-рамкой:
"Что если...", "Представь, что...", "Мысленный эксперимент:", "Вообрази,
что..." или похожей - чтобы читателю сразу было понятно: дальше игра
воображения, а не реальная новость или прогноз.

Тон - с иронией и лёгкостью, это развлекательный контент, а не аналитика
и не сигнал на сделку.

СТРОГО ЗАПРЕЩЕНО:
- любые цифры вообще - проценты, цены, даты, суммы (ни реальные, ни
  гипотетические - сценарий должен быть про идею, а не про числа);
- звучать как реальный прогноз, инсайд, сигнал на сделку или финансовый
  совет - в конце должно оставаться однозначно понятно, что это шутка/
  мысленный эксперимент, а не заявление о будущем;
- шаблонные ИИ-обороты.

Отвечай только текстом поста, без пояснений и без кавычек.""" + voice_guidelines.STYLE_DIRECTIVE


def pick_ticker(last_ticker: Optional[str]) -> str:
    """Выбирает тикер, отличный от последнего использованного - тот же
    паттерн, что и opinion_generator.pick_theme/binance_promo.pick_theme."""
    tickers = list(_TICKERS)
    if last_ticker in tickers and len(tickers) > 1:
        tickers = [t for t in tickers if t != last_ticker]
    return random.choice(tickers)


def generate_hypothetical_post(ticker: str, hook_mode: Optional[str] = None) -> Optional[str]:
    """Возвращает готовый текст поста (хук + дисклеймер), либо None,
    если LLM не смогла выдать нормальный текст.

    Как и binance_promo_generator - нет реальных чисел, которые нужно
    передавать LLM и потом сверять, поэтому возвращаемое значение -
    просто строка (validate_hypothetical_post проверяет, что чисел в
    ответе вообще нет)."""
    system_prompt = _SYSTEM_PROMPT.replace("$TICKER", f"${ticker}")
    if hook_mode is not None:
        system_prompt = f"{system_prompt}\n\n{HOOK_MODES[hook_mode]}"

    user_prompt = (
        f"Тикер для сценария: ${ticker}.\n\n"
        f"Напиши гипотетический 'а что если?' пост про эту монету - "
        f"без единой цифры, с явной фразой-рамкой в начале или по тексту."
    )
    user_prompt += voice_memory.anti_repeat_block()

    hook = call_groq(system_prompt, user_prompt, max_tokens=350, temperature=1.05)

    # Та же подстраховка, что у остальных форматов - call_groq уже
    # перезапрашивает пустые ответы сам, это защита на крайний случай.
    if len(hook.strip()) < 10:
        logger.warning("Хук 'а что если?' пустой или слишком короткий (%r) - пропускаю это окно", hook)
        return None

    text = assemble_post(hook)
    logger.info("Сгенерирован пост 'а что если?' (тикер %s): %s", ticker, text)
    return text


def validate_hypothetical_post(text: str) -> tuple:
    """Как validate_binance_promo - чисел в тексте быть не должно вообще
    (нет реальных данных, которые LLM могла бы легитимно процитировать).
    Дополнительно (специфично для этого формата) - требуем хотя бы одну
    "рамочную" фразу (см. _FRAMING_MARKERS), чтобы гипотетический
    сценарий не читался как настоящее заявление о рынке."""
    numbers = re.findall(r"\d+[.,]?\d*\s*%|\$\s?\d+[.,]?\d*|\d+[.,]?\d*", text)
    if numbers:
        return False, f"В посте 'а что если?' не должно быть чисел, но найдены: {numbers}"

    lowered = text.lower()
    if not any(marker in lowered for marker in _FRAMING_MARKERS):
        return False, "В посте нет явной фразы-рамки ('что если', 'представь' и т.п.) - сценарий может прочитаться как реальное заявление"

    if DISCLAIMER.lower() not in lowered:
        return False, "В тексте отсутствует дисклеймер"

    suspicious = validator.find_suspicious_english_words(text)
    if suspicious:
        return False, f"В тексте похоже есть посторонние английские слова (смешение языков): {', '.join(suspicious[:5])}"

    cliche_ok, found = cliche_filter.check_cliches(text)
    if not cliche_ok:
        return False, f"В тексте есть шаблонные ИИ-фразы: {', '.join(found)}"

    return True, ""
