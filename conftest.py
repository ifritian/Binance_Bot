import pytest

import config


@pytest.fixture(autouse=True)
def _no_random_emoji_accent(monkeypatch):
    """Эмодзи-акцент случаен: в тестах отключён, чтобы не менять начало текста постов."""
    monkeypatch.setattr(config, "EMOJI_ACCENT_ENABLED", False)
