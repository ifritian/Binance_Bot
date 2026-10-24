"""Единое форматирование мелких цен: 3 значащие цифры, без экспоненты (PEPE 5.06e-06 -> 0.00000506)."""
import math


def small_price(p: float) -> str:
    if p <= 0:
        return "0"
    d = max(2, -math.floor(math.log10(p)) + 2)
    return f"{p:.{d}f}"


import re

_NEG = re.compile(r"(?<![\w$.\-])-(?=\d[\d.,]*\s?%)")


def fix_minus(text: str) -> str:
    """-4.0% -> −4.0% (единый знак минуса во всех постах)."""
    return _NEG.sub("−", text) if text else text
