"""
Генерация графика цены для тикера.

Раньше график рисовался через CoinGecko, потому что обычный
api.binance.com геоблокирован - но у этого было два постоянных
побочных эффекта:
1. Тикеры на CoinGecko не уникальны - можно случайно подтянуть график
   совсем другой монеты с тем же symbol (числа на картинке не совпадали
   с числами в тексте поста).
2. У многих мелких/новых пар, которые реально торгуются на Binance,
   на CoinGecko просто нет данных - "Недостаточно данных для графика",
   и пост с таким тикером навечно зависал в очереди, блокируя публикацию
   всего остального (т.к. это был самый высокий score в очереди и его
   продолжали выбирать снова и снова).

Решение - рисовать график из ТОГО ЖЕ источника, по которому сканер
(scanner.py) и нашёл сигнал: data-api.binance.vision. Это публичное
зеркало рыночных данных Binance без авторизации и без гео-ограничений
(в отличие от обычного api.binance.com). Раз сканер увидел этот тикер
и посчитал по нему RSI/Bollinger - значит у Binance по определению
есть свечи по этой паре, и обе проблемы выше отпадают сами собой:
тикер не может "не найтись" или "оказаться другой монетой", потому
что это буквально тот же символ (TICKERUSDT), что и в сигнале.
"""
import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

import config

logger = logging.getLogger(__name__)

_BASE_URL = "https://data-api.binance.vision/api/v3"
_CHARTS_DIR = config.BASE_DIR / "charts"

# Страховка на случай ошибок форматирования тикера и т.п. - на практике
# не должна срабатывать, раз график и сигнал теперь берут данные из
# одного и того же места и по одному и тому же символу.
MAX_PRICE_MISMATCH_RATIO = 3.0

# (interval, limit) для каждого периода графика. Лимит klines у
# Binance - 1000 свечей за запрос, так что запас большой.
_INTERVAL_BY_DAYS = {
    2: ("1h", 48),
    7: ("4h", 42),
}


def fetch_klines(ticker: str, days: int = 2) -> list[float]:
    """Возвращает цены закрытия по тикеру с Binance (data-api.binance.vision).
    ticker - без USDT (например, "PHB" или "BTC")."""
    clean_ticker = ticker.replace("USDT", "").upper()
    symbol = f"{clean_ticker}USDT"
    interval, limit = _INTERVAL_BY_DAYS.get(days, ("1h", days * 24))

    try:
        resp = requests.get(
            f"{_BASE_URL}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
    except requests.RequestException as e:
        logger.warning("Не удалось получить свечи Binance для графика %s: %s", symbol, e)
        return []

    if not isinstance(rows, list):
        # Binance отвечает {"code": ..., "msg": ...} для несуществующего символа
        logger.warning("Binance вернул неожиданный ответ для графика %s: %s", symbol, rows)
        return []

    # формат свечи: [open_time, open, high, low, close, volume, ...]
    try:
        return [float(r[4]) for r in rows]
    except (IndexError, ValueError, TypeError) as e:
        logger.warning("Не удалось разобрать свечи Binance для графика %s: %s", symbol, e)
        return []


def generate_chart_image(ticker: str, days: int = 2, expected_price: float | None = None) -> Path | None:
    """
    Возвращает путь к PNG с графиком цены тикера, либо None.
    """
    try:
        closes = fetch_klines(ticker, days)
    except Exception as e:
        logger.warning("Ошибка при получении данных для графика %s: %s", ticker, e)
        return None

    if not closes or len(closes) < 2:
        logger.warning("Недостаточно данных для графика %s", ticker)
        return None

    if expected_price is not None and expected_price > 0 and closes[-1] > 0:
        ratio = max(closes[-1], expected_price) / min(closes[-1], expected_price)
        if ratio > MAX_PRICE_MISMATCH_RATIO:
            logger.warning(
                "График %s отбракован: последняя цена с Binance (%.10g) сильно "
                "отличается от цены сигнала (%.10g) - %.1fx. Публикация без графика.",
                ticker, closes[-1], expected_price, ratio,
            )
            return None

    times = list(range(len(closes)))

    _CHARTS_DIR.mkdir(exist_ok=True)
    out_path = _CHARTS_DIR / f"{ticker}_chart.png"

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    color = "#0ECB81" if closes[-1] >= closes[0] else "#F6465D"
    ax.plot(times, closes, color=color, linewidth=2)
    ax.fill_between(times, closes, min(closes), color=color, alpha=0.08)

    ax.set_title(f"{ticker} • {days}d", color="white", fontsize=14, loc="left")
    ax.set_facecolor("#0B0E11")
    fig.patch.set_facecolor("#0B0E11")
    ax.tick_params(colors="#848E9C")
    for spine in ax.spines.values():
        spine.set_color("#1E2329")
    ax.grid(color="#1E2329", linewidth=0.5)
    ax.set_xticks([])

    fig.tight_layout()
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)

    logger.info("График сохранён: %s", out_path)
    return out_path
