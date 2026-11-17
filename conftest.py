import pytest

import config


@pytest.fixture(autouse=True)
def _no_random_emoji_accent(monkeypatch):
    """Эмодзи-акцент случаен: в тестах отключён, чтобы не менять начало текста постов."""
    monkeypatch.setattr(config, "EMOJI_ACCENT_ENABLED", False)


@pytest.fixture(autouse=True)
def _square_window_always_open(monkeypatch):
    """Окно активных часов Square (06-21 UTC) зависит от времени запуска тестов: в тестах оно открыто всегда."""
    monkeypatch.setattr(config, "SQUARE_ACTIVE_HOUR_START_UTC", 0)
    monkeypatch.setattr(config, "SQUARE_ACTIVE_HOUR_END_UTC", 24)
