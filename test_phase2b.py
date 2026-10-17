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


def test_promo_disabled_by_default(monkeypatch):
    assert config.BINANCE_PROMO_ENABLED is False
    called = []
    import queue_manager
    queue_manager.set_last_post_time("binance_promo", 0)
    monkeypatch.setattr(main.binance_promo_generator, "generate_binance_promo", lambda *a, **k: called.append(1))
    main.try_publish_binance_promo()
    assert called == []
