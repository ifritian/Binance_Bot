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
from datetime import datetime
from typing import Optional

from groq_client import call_groq
import post_format
import queue_manager
from treasury_index import (
    TreasuryIndexResult, compute_index, fetch_reference_change_pct, format_index_block, leading_tier,
)

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
(если Риск обгоняет Фундамент - аппетит к риску растёт, и наоборот),
а также о том, обогнал ли индекс BTC за период и как дела с момента
запуска (эти цифры тебе даны отдельно) - но без выдумывания новостей,
которых тебе не давали.

Тебе дан набор реальных чисел (см. задание) - если используешь цифры в
хуке, то ТОЧНО как даны, не округляя и не придумывая других. Можно
вообще не называть цифры в хуке (они и так есть в блоке ниже) - просто
качественная рефлексия тоже подходит.

НЕ добавляй сам дисклеймер и НЕ дублируй числовой блок. Отвечай только
текстом хука, без пояснений и без кавычек."""


def _extract_numbers(text: str) -> set[float]:
    return {round(float(n), 2) for n in re.findall(r"[+-]?\d+\.?\d*", text.replace(",", ""))}


def _sign(pct: float) -> str:
    return "+" if pct >= 0 else ""


def _format_comparison_block(
    result: TreasuryIndexResult, period_hours: float,
    btc_pct: Optional[float], history: Optional[dict],
) -> str:
    """Собирает (кодом, не LLM) две строки-"крючка" для шеринга:
    - расхождение с BTC за тот же период - самая "цитируемая" метрика
      поста (интереснее голого числа индекса самого по себе);
    - кумулятивный трекер с момента запуска (индекс vs BTC, база 100) -
      придаёт постам ощущение "истории", за которой можно следить
      неделями, а не разового снимка.
    Возвращает пустую строку, если ни того ни другого посчитать не
    удалось (BTC недоступен) - остальной пост публикуется как обычно."""
    lines = []

    if btc_pct is not None:
        diff = round(result.total_pct - btc_pct, 2)
        if diff > 0:
            verb = f"обогнал BTC на {diff} п.п."
        elif diff < 0:
            verb = f"отстал от BTC на {abs(diff)} п.п."
        else:
            verb = "наравне с BTC"
        lines.append(
            f"🆚 За {period_hours:g}ч: Индекс {_sign(result.total_pct)}{result.total_pct}% "
            f"vs BTC {_sign(btc_pct)}{btc_pct}% - {verb}"
        )

    if history is not None:
        launch_date = datetime.fromtimestamp(history["launch_at"]).strftime("%d.%m.%Y")
        idx_cum = round(history["index_value"] - 100, 2)
        btc_cum = round(history["btc_value"] - 100, 2)
        lines.append(
            f"📈 С запуска ({launch_date}): Индекс {_sign(idx_cum)}{idx_cum}% | BTC {_sign(btc_cum)}{btc_cum}%"
        )

    return "\n".join(lines)


def generate_treasury_post(period_hours: float = 12.0) -> Optional[tuple[str, str, TreasuryIndexResult]]:
    """Возвращает (текст для Binance Square, текст для кросспоста в Telegram,
    TreasuryIndexResult), либо None, если индекс не удалось посчитать
    вообще (ни один тир не собрался - например, полностью недоступен
    data-api.binance.vision).

    Сейчас оба текста идентичны - ссылка на Telegram-канал в пост для
    Binance Square НЕ добавляется (площадка блокирует такие посты
    модерацией, см. комментарий внутри функции), возврат двух отдельных
    строк сохранён на будущее, если для одной из площадок понадобится
    другое форматирование.

    Поднимает groq_client.GroqRateLimited при 429 от Groq - вызывающий
    код (main.py) уже умеет это ловить и выставлять backoff, как для
    opinion/article."""
    result = compute_index(period_hours=period_hours)
    if result.total_pct is None:
        logger.warning("Treasury Index: не удалось посчитать ни один тир - пропускаю публикацию")
        return None

    index_block = format_index_block(result)
    allowed_numbers = _extract_numbers(index_block) | {period_hours}

    # Сравнение с BTC за тот же период + кумулятивный трекер с запуска.
    # Если BTC недоступен (сетевой сбой) - просто публикуем пост без
    # этого блока, не блокируем публикацию из-за одного тикера.
    btc_pct = fetch_reference_change_pct("BTCUSDT", period_hours)
    history = None
    if btc_pct is not None:
        history = queue_manager.update_treasury_history(result.total_pct, btc_pct)
    else:
        logger.warning("Не удалось получить BTC для сравнения - публикую Treasury Index без блока сравнения")

    comparison_block = _format_comparison_block(result, period_hours, btc_pct, history)
    if comparison_block:
        allowed_numbers |= _extract_numbers(comparison_block)

    lt = leading_tier(result)
    lead_line = ""
    if lt is not None:
        sign = "+" if lt.pct >= 0 else ""
        lead_line = f"Лидирует тир: {lt.label} ({sign}{lt.pct}%).\n"

    total_sign = "+" if result.total_pct >= 0 else ""
    comparison_line = f"\n{comparison_block}" if comparison_block else ""
    user_prompt = (
        f"Итоговое изменение индекса за {period_hours:g}ч: {total_sign}{result.total_pct}%.\n"
        f"{lead_line}"
        f"{comparison_line}\n"
        f"Числовой блок целиком (для контекста, НЕ копируй его в ответ):\n{index_block}\n\n"
        f"Напиши короткий хук, который встанет перед этим блоком. Если расхождение "
        f"с BTC или результат с запуска заметные - можно на это указать (см. цифры выше)."
    )

    # GroqRateLimited намеренно НЕ ловится здесь - пробрасывается в main.py,
    # чтобы использовать общий backoff по Retry-After (как в article/opinion).
    hook = call_groq(_SYSTEM_PROMPT, user_prompt, max_tokens=250, temperature=0.9)

    ok, reason = validate_treasury_hook(hook, allowed_numbers)
    if not ok:
        logger.warning("Хук Treasury Index не прошёл проверку (%s) - публикую с нейтральным хуком", reason)
        hook = "📊 Свежий срез Treasury Index:"

    text_parts = [hook.strip(), index_block]
    if comparison_block:
        text_parts.append(comparison_block)
    text_parts.append(post_format.DISCLAIMER)
    binance_text = "\n\n".join(text_parts)

    # Ссылку на Telegram-канал в пост для Binance Square НЕ добавляем -
    # площадка блокирует такие посты модерацией с причиной "promotes
    # third-party channels" (проверено на практике - пост так и не
    # опубликовался, завис в черновиках). post_format.telegram_channel_line()
    # оставлена в кодовой базе на случай, если понадобится для других
    # целей (например, для будущих постов ИСКЛЮЧИТЕЛЬНО в самом Telegram),
    # но сюда, в текст для Binance, сознательно не подключается.
    telegram_text = binance_text

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