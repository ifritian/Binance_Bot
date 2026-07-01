"""
Общие константы и сборка финального текста поста - используется всеми
генераторами (text_generator, opinion_generator, article_generator),
чтобы дисклеймер и структура были одинаковыми во всех форматах.
"""
import config

# Фиксированная фраза дисклеймера - меняй только здесь.
DISCLAIMER = "Информационный пост, не финансовая рекомендация."


# Реферальная ссылка Binance - добавляется только в "мнение" и "статью"
# (опционально, через assemble_post(..., include_referral=True)), НЕ в
# частые валютные сигналы/картинки - там посты выходят по 6-12 раз в
# день, и одна и та же ссылка в каждом выглядела бы как спам и могла бы
# триггернуть модерацию Square.
REFERRAL_LINK = "https://www.binance.com/register?ref=ES7YTYML"
REFERRAL_LINE = f"Открыть аккаунт на Binance: {REFERRAL_LINK}"


def telegram_channel_line() -> str | None:
    """Строка со ссылкой на наш Telegram-канал (config.TELEGRAM_PUBLISH_CHANNEL) -
    добавляется в посты на Binance Square, чтобы читатели могли перейти
    в Telegram. Возвращает None, если канал не настроен ИЛИ настроен как
    приватный (числовой chat_id, например "-1001234567890") - у приватных
    каналов нет публичной t.me-ссылки, вести туда некуда, и молча
    подставлять битую ссылку не нужно.

    Публичный канал должен быть задан в конфиге с "@" в начале
    (например TELEGRAM_PUBLISH_CHANNEL=@my_channel)."""
    channel = config.TELEGRAM_PUBLISH_CHANNEL
    if not channel or not str(channel).startswith("@"):
        return None
    username = str(channel).lstrip("@")
    return f"📣 Подробнее и другие посты - в нашем Telegram: https://t.me/{username}"


def assemble_post(hook: str, include_referral: bool = False) -> str:
    """Хук + пустая строка + дисклеймер (+ опционально реферальная
    ссылка отдельной строкой) - структура фиксирована кодом, не
    оставлена на волю LLM."""
    parts = [hook.strip(), DISCLAIMER]
    if include_referral:
        parts.append(REFERRAL_LINE)
    return "\n\n".join(parts)


def assemble_signal_post(hook: str, signal) -> str:
    """Хук (от LLM) + блок сетапа (вход/стоп/тейк/RSI/score - собран
    КОДОМ, не LLM, чтобы цифры были гарантированно точными) + дисклеймер.

    signal - RsiSignal из signal_parser.
    """
    direction_emoji = "🟢" if "лонг" in signal.direction.lower() else "🔴"

    setup_lines = [
        f"{direction_emoji} {signal.direction} | {signal.strategy}",
        f"Вход: {signal.entry_low} - {signal.entry_high}",
        f"Стоп: {signal.invalidation}",
        f"Тейк: {signal.target}",
        f"RSI: {signal.rsi_now} | Score: {signal.score}/100",
    ]
    setup_block = "\n".join(setup_lines)

    return f"{hook.strip()}\n\n{setup_block}\n\n{DISCLAIMER}"


# --- Режимы тона хука - для разнообразия постов ---
# Каждый режим - короткая инструкция, которую добавляем к системному
# промпту LLM. Сама ротация (какой режим выбрать сейчас) реализована
# в pick_hook_mode ниже - избегаем повтора последнего использованного.

HOOK_MODES: dict[str, str] = {
    "question": (
        "Тон: наблюдение + риторический вопрос в конце. "
        "Например: 'интересно, как далеко пойдёт' / 'это всё, или будет больше?'"
    ),
    "statement": (
        "Тон: уверенное утверждение, без вопроса в конце. "
        "Звучит как личное мнение, высказанное прямо, без сомнений в формулировке."
    ),
    "comparison": (
        "Тон: сравнение с тем, что обычно ожидается в такой ситуации. "
        "Например: 'обычно после такого роста бывает откат, но...' - "
        "подай факт через контраст с типичным паттерном."
    ),
    "playful": (
        "Тон: лёгкий, слегка ироничный, можно с шуткой или неожиданным "
        "сравнением. Без вопроса в конце - просто живая реакция."
    ),
}


def pick_hook_mode(last_mode: str | None) -> str:
    """Выбирает режим тона, отличный от последнего использованного,
    чтобы посты не звучали одинаково раз за разом."""
    import random

    modes = list(HOOK_MODES.keys())
    if last_mode in modes and len(modes) > 1:
        modes = [m for m in modes if m != last_mode]
    return random.choice(modes)