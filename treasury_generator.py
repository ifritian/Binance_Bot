"""
Генератор поста "Treasury Index" - публикуется раз в TREASURY_INTERVAL_HOURS
(конфиг), независимо от currency/opinion/article.

Сам индекс (состав корзины, веса, расчёт %, защита от выбросов) считается
ПОЛНОСТЬЮ кодом - см. treasury_index.py. LLM здесь получает уже готовый
числовой блок как контекст и пишет только короткий хук ПЕРЕД ним - не
переписывает и не придумывает цифры внутри блока, ровно та же идея, что
в opinion_generator/article_generator.
"""
import logging
import re
from typing import Optional

from groq_client import call_groq
import post_format
from treasury_index import TreasuryIndexResult, compute_index, format_index_block, leading_tier

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ты пишешь короткий хук (вводную фразу) для поста
Treasury Index - собственного инфраструктурного крипто-индекса канала
(L1/L2/DeFi монеты без BTC/ETH/BNB, разбитые на три уровня риска:
Фундамент/Рост/Риск). Хук идёт ПЕРЕД готовым числовым блоком индекса -
сам блок тебе показан только для контекста, повторять его в ответе не
нужно, он будет добавлен отдельно после твоего текста.

1-3 предложения, живой разговорный стиль, без канцелярита и без
воды. Можно лёгкую рефлексию о том, что означает движение - например,
какой тир лидирует и что это может говорить о риск-аппетите рынка
(если Риск обгоняет Фундамент - аппетит к риску растёт, и наоборот) -
но без выдумывания новостей, которых тебе не давали.

Тебе дан набор реальных чисел (см. задание) - если используешь цифры в
хуке, то ТОЧНО как даны, не округляя и не придумывая других. Можно
вообще не называть цифры в хуке (они и так есть в блоке ниже) - просто
качественная рефлексия тоже подходит.

НЕ добавляй сам дисклеймер и НЕ дублируй числовой блок. Отвечай только
текстом хука, без пояснений и без кавычек."""


def _extract_numbers(text: str) -> set[float]:
    return {round(float(n), 2) for n in re.findall(r"[+-]?\d+\.?\d*", text.replace(",", ""))}


def generate_treasury_post(period_hours: float = 12.0) -> Optional[tuple[str, str, TreasuryIndexResult]]:
    """Возвращает (текст для Binance Square, текст для кросспоста в Telegram,
    TreasuryIndexResult), либо None, если индекс не удалось посчитать
    вообще (ни один тир не собрался - например, полностью недоступен
    data-api.binance.vision).

    Тексты отличаются только наличием ссылки на Telegram-канал - она
    нужна на Binance Square (чтобы звать читателя в Telegram), но
    бессмысленна при кросспосте того же поста обратно в тот же канал.

    Поднимает groq_client.GroqRateLimited при 429 от Groq - вызывающий
    код (main.py) уже умеет это ловить и выставлять backoff, как для
    opinion/article."""
    result = compute_index(period_hours=period_hours)
    if result.total_pct is None:
        logger.warning("Treasury Index: не удалось посчитать ни один тир - пропускаю публикацию")
        return None

    index_block = format_index_block(result)
    allowed_numbers = _extract_numbers(index_block) | {period_hours}

    lt = leading_tier(result)
    lead_line = ""
    if lt is not None:
        sign = "+" if lt.pct >= 0 else ""
        lead_line = f"Лидирует тир: {lt.label} ({sign}{lt.pct}%).\n"

    total_sign = "+" if result.total_pct >= 0 else ""
    user_prompt = (
        f"Итоговое изменение индекса за {period_hours:g}ч: {total_sign}{result.total_pct}%.\n"
        f"{lead_line}"
        f"Числовой блок целиком (для контекста, НЕ копируй его в ответ):\n{index_block}\n\n"
        f"Напиши короткий хук, который встанет перед этим блоком."
    )

    # GroqRateLimited намеренно НЕ ловится здесь - пробрасывается в main.py,
    # чтобы использовать общий backoff по Retry-After (как в article/opinion).
    hook = call_groq(_SYSTEM_PROMPT, user_prompt, max_tokens=250, temperature=0.9)

    ok, reason = validate_treasury_hook(hook, allowed_numbers)
    if not ok:
        logger.warning("Хук Treasury Index не прошёл проверку (%s) - публикую с нейтральным хуком", reason)
        hook = "📊 Свежий срез Treasury Index:"

    text_parts = [hook.strip(), index_block, post_format.DISCLAIMER]
    binance_text = "\n\n".join(text_parts)

    # Ссылка на Telegram-канал имеет смысл только в посте на Binance Square
    # (зовёт читателя перейти в Telegram) - при кросспосте ЭТОГО ЖЕ поста
    # обратно в тот же Telegram-канал ссылка на самого себя бессмысленна,
    # поэтому для кросспоста используется текст без неё.
    telegram_line = post_format.telegram_channel_line()
    telegram_text = binance_text
    if telegram_line:
        binance_text = binance_text + "\n\n" + telegram_line

    logger.info("Сгенерирован пост Treasury Index (%s%%, лидер %s): %s",
                total_sign + str(result.total_pct), lt.key if lt else "нет", binance_text[:150].replace("\n", " "))
    return binance_text, telegram_text, result


def validate_treasury_hook(hook: str, allowed_numbers: set[float]) -> tuple[bool, str]:
    """Хук не должен содержать чисел, которых нет среди уже посчитанных
    (числового блока). Сам числовой блок в проверку не входит - он
    собран кодом и по определению корректен."""
    numbers = _extract_numbers(hook)
    unknown = [n for n in numbers if not any(abs(n - a) < 0.05 for a in allowed_numbers)]
    if unknown:
        return False, f"В хуке есть числа не из посчитанных данных: {unknown}"
    return True, ""