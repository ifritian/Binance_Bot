"""
Парсинг постов канала @resultrsi (дайджест "Follow-up").

Формат поста (пример):

Top follow-up winners by 18:00 EEST

Best results that kept moving in the original thesis direction today:

1. HEIUSDT | 8h | favorable +7.32% | score 100
2. ALTUSDT | 4h | favorable +3.10% | score 91

This digest is based on completed follow-up checks, not raw alerts.

В отличие от прежнего формата (вход/цель/стоп), здесь нет торгового
сигнала на вход - это статистика по уже отработавшим сигналам.
Поэтому числа, которые нельзя искажать - это % изменения и score,
а не уровни цены.
"""
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class FollowUpEntry:
    ticker: str             # HEI
    timeframe: str           # 8h
    result: str               # favorable / unfavorable
    change_pct: str          # +7.32%
    score: int


@dataclass
class Signal:
    title: str                       # "Top follow-up winners by 18:00 EEST"
    entries: list[FollowUpEntry]
    raw_text: str

    @property
    def top(self) -> FollowUpEntry:
        """Первая (лучшая/первая по списку) запись дайджеста."""
        return self.entries[0]


_TITLE_RE = re.compile(r"^(Top follow-up \w+ by .+?)$", re.MULTILINE)
_ENTRY_RE = re.compile(
    r"\d+\.\s*([A-Z0-9]+)USDT\s*\|\s*(\d+\w+)\s*\|\s*(favorable|unfavorable)\s*"
    r"([+-]?[\d.]+%)\s*\|\s*score\s*(\d+)",
    re.IGNORECASE,
)


def is_signal_message(text: str) -> bool:
    """Похож ли текст на дайджест follow-up (а не просто картинка без подписи)."""
    return bool(_ENTRY_RE.search(text))


def parse_signal(text: str) -> Optional[Signal]:
    if not is_signal_message(text):
        return None

    title_match = _TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else "Follow-up digest"

    entries = []
    for m in _ENTRY_RE.finditer(text):
        entries.append(
            FollowUpEntry(
                ticker=m.group(1),
                timeframe=m.group(2),
                result=m.group(3).lower(),
                change_pct=m.group(4),
                score=int(m.group(5)),
            )
        )

    if not entries:
        return None

    return Signal(title=title, entries=entries, raw_text=text)