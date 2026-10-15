import cliche_filter
import groq_client
import signal_explainer
import square_question
import market_digest
import voice_guidelines


def test_humanize_dashes():
    assert voice_guidelines.humanize("BTC растёт — и это странно.") == "BTC растёт - и это странно."
    assert voice_guidelines.humanize("а—б") == "а - б"
    assert "—" not in voice_guidelines.humanize("x — y – z")
    assert voice_guidelines.humanize("строка 1\n\nстрока 2") == "строка 1\n\nстрока 2"


def test_new_cliches_caught():
    for t in ("Цена упала, будто дверь захлопнулась", "Словно американские горки", "Время покажет"):
        ok, found = cliche_filter.check_cliches(t)
        assert not ok and found


def test_groq_output_humanized(monkeypatch):
    monkeypatch.setattr(groq_client, "_request_once", lambda *a, **k: ("Рост — это хорошо, но осторожно.", "stop"))
    assert groq_client.call_groq("s", "u") == "Рост - это хорошо, но осторожно."


def test_our_static_templates_are_clean():
    texts = list(square_question.TEMPLATES) + list(square_question.TEMPLATES_EN)
    texts += market_digest._QUESTIONS_GENERIC + market_digest._EN_QUESTIONS_GENERIC + market_digest._HOT_TEMPLATES + market_digest._EN_HOT_TEMPLATES
    for t in texts:
        assert "—" not in t, t
        assert cliche_filter.check_cliches(t)[0], t
