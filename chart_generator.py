"""
Генерация графика цены для тикера из сигнала, на основе публичного
Binance Market Data API (klines) - без ключей, без авторизации.

График сохраняется как PNG и передаётся в binance_publisher для
загрузки вместе с постом.
"""
import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # без GUI - рисуем прямо в файл
import matplotlib.pyplot as plt
import requests

import config

logger = logging.getLogger(__name__)

_KLINES_URL = "https://api.binance.com/api/v3/klines"
_CHARTS_DIR = config.BASE_DIR / "charts"


def fetch_klines(symbol: str, interval: str = "1h", limit: int = 48) -> list:
    """Публичная функция - переиспользуется в opinion_generator.py
    для расчёта % изменения BTC за период без участия LLM."""
    params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}
    resp = requests.get(_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def generate_chart_image(ticker: str, interval: str = "1h", limit: int = 48) -> Path | None:
    """
    Возвращает путь к PNG с графиком цены тикера, либо None, если
    данные не удалось получить (например, тикер не торгуется на спот-
    рынке Binance под таким именем - тогда пост просто уйдёт без графика).
    """
    try:
        klines = fetch_klines(ticker, interval, limit)
    except requests.RequestException as e:
        logger.warning("Не удалось получить данные для графика %s: %s", ticker, e)
        return None

    if not klines:
        return None

    closes = [float(k[4]) for k in klines]
    times = list(range(len(closes)))

    _CHARTS_DIR.mkdir(exist_ok=True)
    out_path = _CHARTS_DIR / f"{ticker}_{interval}.png"

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    color = "#0ECB81" if closes[-1] >= closes[0] else "#F6465D"  # зелёный/красный как в Binance
    ax.plot(times, closes, color=color, linewidth=2)
    ax.fill_between(times, closes, min(closes), color=color, alpha=0.08)

    ax.set_title(f"{ticker}USDT • {interval}", color="white", fontsize=14, loc="left")
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