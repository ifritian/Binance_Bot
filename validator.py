"""
Защита от искажения чисел LLM-ом.

Структурный блок с уровнями (вход/стоп/тейк/RSI/score) для сигналов
из @syndicateproobot собирается КОДОМ (post_format.assemble_signal_post),
а не LLM - поэтому риск искажения там в принципе исключён. Здесь мы
дополнительно проверяем, что итоговый текст действительно содержит
этот код-блок целиком (на случай ошибки сборки), и что хук от LLM не
содержит посторонних чисел, которые могли бы выглядеть как другой
(неверный) уровень входа/стопа/тейка.
"""
import re

from post_format import DISCLAIMER
from signal_parser import RsiSignal

_PERCENT_RE = re.compile(r"[+-]?\d+\.?\d*%")
_NUMBER_RE = re.compile(r"\d+\.?\d*")

# Ловит смешение языков в хуке (баг был: few-shot примеры на английском
# при русских инструкциях -> модель иногда выдавала кашу вроде "в a
# крутом sprint вниз, волatility like on American ropges"). Тикеры,
# аббревиатуры (RSI, USDT) и cashtag'и обычно ЗАГЛАВНЫМИ - их не трогаем,
# подозрительны только строчные/смешанные латинские слова от 3 букв -
# это почти всегда настоящие английские слова, а не тикер.
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_URL_RE = re.compile(r"https?://\S+")
# Слова из наших же code-generated блоков (post_format.assemble_signal_post
# пишет "RSI: ... | Score: .../100", accuracy_report_generator пишет
# "Win-rate: ...") - не смешение языков, а часть шаблона.
_ALLOWED_ENGLISH_WORDS = {"rsi", "score", "win", "rate"}


def find_suspicious_english_words(text: str) -> list[str]:
    """Возвращает список слов, похожих на случайно затесавшийся
    английский текст (не тикеры/акронимы/ссылки/слова шаблона). Пустой
    список - текст в порядке."""
    cleaned = _URL_RE.sub("", text)
    words = _LATIN_WORD_RE.findall(cleaned)
    return [w for w in words if not w.isupper() and w.lower() not in _ALLOWED_ENGLISH_WORDS]


def validate_post_text(text: str, signal: RsiSignal) -> tuple[bool, str]:
    """
    Возвращает (ok, причина_отказа). ok=True значит пост безопасен
    для публикации - все ключевые уровни сигнала присутствуют в тексте
    точно как в исходных данных, и дисклеймер на месте.
    """
    required_fields = {
        "вход (нижняя граница)": signal.entry_low,
        "вход (верхняя граница)": signal.entry_high,
        "стоп (инвалидация)": signal.invalidation,
        "тейк (цель)": signal.target,
        "RSI": signal.rsi_now,
        "score": signal.score,
    }

    for label, value in required_fields.items():
        if value and value not in text:
            return False, f"В тексте не найден исходный уровень {label}: {value}"

    if DISCLAIMER.lower() not in text.lower():
        return False, "В тексте отсутствует дисклеймер"

    suspicious = find_suspicious_english_words(text)
    if suspicious:
        return False, f"В тексте похоже есть посторонние английские слова (смешение языков): {', '.join(suspicious[:5])}"

    return True, ""


def validate_image_post_text(text: str) -> tuple[bool, str]:
    """
    Для постов по картинке у нас нет надёжных исходных чисел (vision-
    распознавание ненадёжно для цифр), поэтому правило обратное:
    в финальном тексте НЕ должно быть вообще никаких чисел - это
    значит, что LLM не придумал цифры от себя.
    """
    if _NUMBER_RE.search(text):
        return False, "В тексте по картинке не должно быть чисел, но они найдены"

    if DISCLAIMER.lower() not in text.lower():
        return False, "В тексте отсутствует дисклеймер"

    suspicious = find_suspicious_english_words(text)
    if suspicious:
        return False, f"В тексте похоже есть посторонние английские слова (смешение языков): {', '.join(suspicious[:5])}"

    return True, ""