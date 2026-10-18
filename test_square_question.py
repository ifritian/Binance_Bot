import pytest
import config
import main
import square_question


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(square_question, "_fetch_changes", lambda coins: {})  # без сети


def test_question_has_cashtag_and_hashtag():
    text, t = square_question.pick_question()
    assert f"${t}" in text and f"#{t}" in text and "$" + "{" not in text


def test_publish_once(monkeypatch):
    sent = []
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_square_question()
    main.try_publish_square_question()
    assert len(sent) == 1


def test_question_english(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    text, t = square_question.pick_question()
    assert f"${t}" in text and not any("а" <= ch <= "я" for ch in text.lower())


# ---------------- вопросы по реальному движению ----------------

CH = {"BTC": -0.4, "ETH": 1.1, "SOL": 6.2, "BNB": 0.2, "XRP": -4.8, "DOGE": 3.1, "SUI": -2.5, "TON": 0.0, "LINK": 2.2, "AVAX": 0.9}


def test_data_question_signs_match_direction(monkeypatch):
    import random
    import config
    for lang in ("en", "ru"):
        monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", lang)
        for seed in range(40):
            text, ticker = square_question.pick_data_question(None, random.Random(seed), CH)
            first = text.split("\n\n")[0]
            pct = CH[ticker]
            assert f"${ticker}" in first and "{" not in first
            if "−" in first:          # знак минус показан только у падающих
                assert pct < 0
            if "+" in first:
                assert pct > 0
            if pct >= 2:
                assert "−" not in first and "just lost" not in first and "down" not in first.lower().split()
            if pct <= -2:
                assert "is up" not in first and "climbing" not in first


def test_data_question_none_without_data_and_falls_back(monkeypatch):
    import random
    assert square_question.pick_data_question(None, random.Random(1), {}) is None
    monkeypatch.setattr(square_question, "_fetch_changes", lambda coins: {})
    text, ticker = square_question.pick_question(None, random.Random(1))
    assert ticker and f"${ticker}" in text   # запасной шаблон


def test_pick_question_uses_data_when_available(monkeypatch):
    import random
    monkeypatch.setattr(square_question, "_fetch_changes", lambda coins: dict(CH))
    text, ticker = square_question.pick_question(None, random.Random(3))
    assert ticker in {"SOL", "XRP", "DOGE", "SUI"} and any(ch in text for ch in "%")
