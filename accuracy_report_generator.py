"""
accuracy_report_generator.py - еженедельный пост со статистикой точности
сигналов (win-rate, средний % результата), на основе outcome_tracker
(см. Фазу 1 - трекинг результатов).

Идея та же, что у treasury_generator: числовой блок собирается ПОЛНОСТЬЮ
кодом из outcome_tracker.get_accuracy_stats() - LLM никогда не видит
задачу "посчитай win-rate" и не может его исказить. LLM только пишет
короткий хук поверх уже готовых цифр, и хук проверяется на отсутствие
посторонних чисел (validate_accuracy_hook), как и в treasury.

Это не только контент, но и работа на доверие: канал честно показывает
результаты сигналов, включая неудачные, а не только победы - для
любой аудитории это единственный способ отличить реальную точность
от рекламы "сигналы, которые работают".
"""
import logging
import re
import time

from groq_client import call_groq
from loss_review_generator import classify_miss
import outcome_tracker
import post_format
import queue_manager
import strategy_tuner

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ты пишешь короткий хук (вводную фразу) для еженедельного
отчёта о точности торговых сигналов канала. Хук идёт ПЕРЕД готовым
числовым блоком со статистикой (win-rate, средний результат в % по
стратегиям) - сам блок тебе показан только для контекста, повторять
его в ответе не нужно, он будет добавлен отдельно после твоего текста.

1-3 предложения, живой разговорный стиль на русском языке, без
канцелярита. Тон честный и спокойный - если неделя была слабой
(win-rate низкий или средний результат отрицательный), НЕ приукрашивай
и не оправдывайся, а просто констатируй факт и, если уместно, добавь
нейтральное наблюдение (например, "не все недели одинаковые" или "будем
разбираться, что пошло не так"). Если неделя была сильной - можно
искреннюю, но не хвастливую радость.

Тебе дан набор реальных чисел (см. задание) - если используешь цифры
в хуке, то ТОЧНО как даны, не округляя иначе и не придумывая других.
Можно вообще не называть цифры в хуке (они и так есть в блоке ниже).

НЕ добавляй сам дисклеймер и НЕ дублируй числовой блок. Отвечай только
текстом хука на русском языке, без пояснений и без кавычек."""

_NUMBER_RE = re.compile(r"[+-]?\d+\.?\d*")


def _extract_numbers(text: str) -> set[float]:
    return {round(float(n), 2) for n in _NUMBER_RE.findall(text.replace(",", ""))}


def _format_stats_block(stats: dict, days: float, period_closed: list[dict] | None = None) -> str:
    overall = stats["overall"]
    lines = [f"📊 Точность сигналов за последние {days:g} дней"]

    wr = f"{overall['win_rate']}%" if overall["win_rate"] is not None else "н/д"
    avg = overall["avg_pnl_pct"]
    avg_str = f"{'+' if avg is not None and avg >= 0 else ''}{avg}%" if avg is not None else "н/д"
    lines.append(f"Всего закрыто: {overall['count']} | Win-rate: {wr} | Средний результат: {avg_str}")

    if stats["by_strategy"]:
        lines.append("\nПо стратегиям:")
        for strat, s in sorted(stats["by_strategy"].items(), key=lambda kv: -kv[1]["count"]):
            if s["count"] == 0:
                continue
            swr = f"{s['win_rate']}%" if s["win_rate"] is not None else "н/д"
            savg = s["avg_pnl_pct"]
            savg_str = f"{'+' if savg is not None and savg >= 0 else ''}{savg}%" if savg is not None else "н/д"
            lines.append(f"  {strat}: n={s['count']}, win-rate={swr}, средний результат={savg_str}")

    # Конкретный разбор худших случаев, а не только агрегаты - использует
    # ту же классификацию по факту движения цены (MFE/время до стопа),
    # что и loss_review_generator, без домыслов о внешних причинах.
    if period_closed:
        negative = [c for c in period_closed if c.get("pnl_pct", 0) < 0]
        if negative:
            worst = sorted(negative, key=lambda c: c["pnl_pct"])[:3]
            lines.append("\nСамые заметные промахи периода:")
            for c in worst:
                direction_ru = "Лонг" if c.get("direction") == "long" else "Шорт"
                lines.append(
                    f"  {c.get('ticker', '?')} | {direction_ru} | {c.get('strategy', '?')} | "
                    f"результат {c['pnl_pct']:+.2f}% -> {classify_miss(c)}"
                )

    # Прозрачность автокоррекции (strategy_tuner) - если бот сам поднял
    # порог публикации для какой-то стратегии из-за слабой статистики,
    # аудитория должна это видеть, а не узнавать постфактум.
    active_adjustments = queue_manager.get_strategy_adjustments()
    if active_adjustments:
        lines.append(f"\nАвтокоррекция: порог публикации временно повышен для {strategy_tuner.describe_active_adjustments()} - по статистике последних {strategy_tuner.TUNING_LOOKBACK_DAYS:g} дней.")

    return "\n".join(lines)


def generate_accuracy_report_post(days: float = 7.0) -> tuple[str, str] | None:
    """Возвращает (текст для Binance Square, текст для кросспоста в
    Telegram) либо None, если данных недостаточно (меньше
    config.ACCURACY_REPORT_MIN_CLOSED_SIGNALS закрытых сигналов за период -
    статистика на таком объёме бессмысленна и только подорвёт доверие).

    Поднимает groq_client.GroqRateLimited при 429 - вызывающий код
    (main.py) уже умеет это ловить, как для treasury/article/opinion."""
    import config

    stats = outcome_tracker.get_accuracy_stats(days=days)
    overall = stats["overall"]

    if overall["count"] < config.ACCURACY_REPORT_MIN_CLOSED_SIGNALS:
        logger.info(
            "Недостаточно закрытых сигналов за %.0f дней (%d < %d) - пропускаю отчёт точности",
            days, overall["count"], config.ACCURACY_REPORT_MIN_CLOSED_SIGNALS,
        )
        return None

    cutoff = time.time() - days * 24 * 3600
    period_closed = [c for c in queue_manager.get_closed_outcomes() if c.get("closed_at", 0) >= cutoff]

    stats_block = _format_stats_block(stats, days, period_closed)
    allowed_numbers = _extract_numbers(stats_block) | {days}

    user_prompt = (
        f"Числовой блок целиком (для контекста, НЕ копируй его в ответ):\n{stats_block}\n\n"
        f"Напиши короткий честный хук, который встанет перед этим блоком."
    )

    hook = call_groq(_SYSTEM_PROMPT, user_prompt, max_tokens=200, temperature=0.8)

    ok, reason = validate_accuracy_hook(hook, allowed_numbers)
    if not ok:
        logger.warning("Хук отчёта точности не прошёл проверку (%s) - публикую с нейтральным хуком", reason)
        hook = "📈 Свежий срез по точности наших сигналов за неделю:"

    text = "\n\n".join([hook.strip(), stats_block, post_format.DISCLAIMER])

    logger.info("Сгенерирован отчёт точности (n=%d, win-rate=%s): %s",
                overall["count"], overall["win_rate"], text[:150].replace("\n", " "))
    return text, text


def validate_accuracy_hook(hook: str, allowed_numbers: set[float]) -> tuple[bool, str]:
    """Хук не должен содержать чисел, которых нет среди уже посчитанной
    статистики - той же логики, что validate_treasury_hook в
    treasury_generator.py."""
    from validator import find_suspicious_english_words

    numbers = _extract_numbers(hook)
    unknown = [n for n in numbers if not any(abs(n - a) < 0.05 for a in allowed_numbers)]
    if unknown:
        return False, f"В хуке есть числа не из посчитанной статистики: {unknown}"

    suspicious = find_suspicious_english_words(hook)
    if suspicious:
        return False, f"В хуке есть посторонние английские слова: {', '.join(suspicious[:5])}"

    return True, ""
