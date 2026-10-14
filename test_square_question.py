import pytest
import config
import main
import square_question


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))


def test_question_has_cashtag_and_hashtag():
    text, t = square_question.pick_question()
    assert f"${t}" in text and f"#{t}" in text and "$" + "{" not in text


def test_publish_once(monkeypatch):
    sent = []
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_square_question()
    main.try_publish_square_question()
    assert len(sent) == 1
