"""
Генерация графика цены для тикера через CoinGecko API (без геоблокировки).
"""
import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

import config

logger = logging.getLogger(__name__)

_COINGECKO_URL = "https://api.coingecko.com/api/v3"
_CHARTS_DIR = config.BASE_DIR / "charts"

# Маппинг тикеров на CoinGecko ID
_TICKER_TO_COINGECKO = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "LTC": "litecoin",
    "ARB": "arbitrum",
    "OP": "optimism",
    "FTM": "fantom",
}


def _get_coingecko_id(ticker: str) -> str:
    """Преобразует тикер в CoinGecko ID."""
    clean_ticker = ticker.replace("USDT", "").upper()
    return _TICKER_TO_COINGECKO.get(clean_ticker, clean_ticker.lower())


def fetch_klines(symbol: str, days: int = 2) -> list:
    """Получает данные цены с CoinGecko за последние N дней."""
    coingecko_id = _get_coingecko_id(symbol)
    
    try:
        url = f"{_COINGECKO_URL}/coins/{coingecko_id}/market_chart"
        params = {
            "vs_currency": "usd",
            "days": days,
            "interval": "hourly" if days <= 2 else "daily"
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        # CoinGecko возвращает [[timestamp, price], ...]
        return data.get("prices", [])
    except requests.RequestException as e:
        logger.warning("Не удалось получить данные CoinGecko для %s: %s", symbol, e)
        return []


def generate_chart_image(ticker: str, days: int = 2) -> Path | None:
    """
    Возвращает путь к PNG с графиком цены тикера, либо None.
    """
    try:
        klines = fetch_klines(ticker, days)
    except Exception as e:
        logger.warning("Ошибка при получении данных для графика %s: %s", ticker, e)
        return None

    if not klines or len(klines) < 2:
        logger.warning("Недостаточно данных для графика %s", ticker)
        return None

    closes = [float(k[1]) for k in klines]
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