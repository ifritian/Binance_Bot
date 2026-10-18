import pytest
import config, main, post_format


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))


SAMPLE = """Хук.

📊 Treasury Index: +1.24% (25ч)

🔵 Фундамент (+2.04%): NEAR +7.1%, SOL +2.1%, AVAX +0.8%
🟡 Рост (+1.0%): AAVE -5.1%, UNI +2.3%
🔴 Риск (+0.5%): SUI +9.2%, PENDLE +3.1%

vs BTC +0.4%

Не финансовый совет."""


def test_cashtag_top_movers():
    out = post_format.cashtag_top_movers(SAMPLE, 3)
    assert "$SUI 9.2%" not in out  # знак процента сохраняется
    assert "$SUI +9.2%" in out and "$NEAR +7.1%" in out and "$AAVE -5.1%" in out
    assert out.count("$") == 3
    assert "SOL +2.1%" in out and "vs BTC +0.4%" in out  # остальное не тронуто


def test_cashtag_top_movers_no_tiers():
    assert post_format.cashtag_top_movers("просто текст BTC +1%") == "просто текст BTC +1%"


def test_no_promo_format_left():
    assert not hasattr(main, "try_publish_binance_promo")
    assert not hasattr(config, "BINANCE_PROMO_ENABLED")


def test_dead_weight_formats_off_by_default():
    assert config.INDEX_SIGNALS_ENABLED is False
    assert config.WIN_CELEBRATIONS_ENABLED is False
    assert config.LEGACY_SCANNER_ENABLED is False
    assert config.BINANCE_CTA_PROBABILITY == 0


def test_cta_lines_have_no_unverifiable_claims():
    for line in post_format._BINANCE_CTA_LINES:
        low = line.lower()
        assert "комисси" not in low and "самых" not in low and "—" not in line


def test_win_context_line_is_honest_and_included():
    import post_format
    stats = {"overall": {"count": 25, "win_rate": 28.0, "avg_pnl_pct": -0.31}}
    line = post_format.win_context_line(stats)
    assert "25" in line and "28" in line and "-0.31" in line
    rec = {"direction": "long", "ticker": "BTC", "strategy": "x", "entry": 1, "exit_price": 2,
           "pnl_pct": 5.0, "hours_to_close": 3}
    text = post_format.assemble_win_celebration_post("Есть!", rec, line)
    assert line in text
    assert post_format.win_context_line({}) == ""


def test_legacy_signal_formats_are_not_scheduled_when_scanner_off(monkeypatch):
    """Очередь старых сигналов, отчёт точности, разбор неудач и недельная статья
    не публикуются, пока LEGACY_SCANNER_ENABLED выключен."""
    import inspect
    src = inspect.getsource(main.tick)
    for name in ("try_publish_currency_post", "try_publish_article_post",
                 "try_publish_accuracy_report", "try_publish_loss_review"):
        i = src.index(f"{name}()")
        assert "LEGACY_SCANNER_ENABLED" in src[max(0, i - 80):i], name
