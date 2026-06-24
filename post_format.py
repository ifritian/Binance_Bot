"""
Общие константы и сборка финального текста поста - используется всеми
генераторами (text_generator, opinion_generator, article_generator),
чтобы дисклеймер и структура были одинаковыми во всех форматах.
"""

# Фиксированная фраза дисклеймера - меняй только здесь.
DISCLAIMER = "Информационный пост, не финансовая рекомендация."


def assemble_post(hook: str) -> str:
    """Хук + пустая строка + дисклеймер - структура фиксирована кодом,
    не оставлена на волю LLM."""
    return f"{hook.strip()}\n\n{DISCLAIMER}"


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