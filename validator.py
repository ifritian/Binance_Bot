"""
Защита от искажения чисел LLM-ом.

После генерации текста проверяем: присутствуют ли в финальном посте
ровно те цифры (% изменения и score), что были в исходном дайджесте.
Если хотя бы одно число не нашлось - публикацию нужно остановить.
"""
import re

from post_format import DISCLAIMER
from signal_parser import FollowUpEntry

_PERCENT_RE = re.compile(r"[+-]?\d+\.?\d*%")
_NUMBER_RE = re.compile(r"\d+\.?\d*")


def validate_post_text(text: str, entry: FollowUpEntry) -> tuple[bool, str]:
    """
    Возвращает (ok, причина_отказа). ok=True значит пост безопасен
    для публикации.
    """
    # Сравниваем % с точностью до запятой/точки и знака - в обоих
    # форматах могут отличаться написания "+7.32%" vs "7.32%",
    # поэтому сравниваем числовое значение, а не строку целиком.
    found_percents = {
        float(p.rstrip("%")) for p in _PERCENT_RE.findall(text)
    }
    target_percent = float(entry.change_pct.rstrip("%"))

    if not any(abs(target_percent - p) < 1e-6 for p in found_percents):
        return False, f"В тексте не найден исходный %: {entry.change_pct}"

    found_numbers = {float(n) for n in _NUMBER_RE.findall(text)}
    if not any(abs(entry.score - n) < 1e-9 for n in found_numbers):
        return False, f"В тексте не найден исходный score: {entry.score}"

    if DISCLAIMER.lower() not in text.lower():
        return False, "В тексте отсутствует дисклеймер"

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

    return True, ""