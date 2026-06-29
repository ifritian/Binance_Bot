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
import queue_manager

logger = logging.getLogger(__name__)

_COINGECKO_URL = "https://api.coingecko.com/api/v3"
_CHARTS_DIR = config.BASE_DIR / "charts"

# Если последняя цена с CoinGecko отличается от цены сигнала с Binance
# больше чем во столько раз - считаем, что _get_coingecko_id() подобрал
# не ту монету (омоним тикера), и график не публикуем.
MAX_PRICE_MISMATCH_RATIO = 3.0

# Маппинг тикеров на CoinGecko ID - быстрый путь для самых частых монет,
# без обращения к /search. Для всего остального ID ищется через API
# (см. _resolve_coingecko_id) и результат кэшируется в bot_state.db.
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


def _search_coingecko_id(clean_ticker: str) -> str | None:
    """Ищет CoinGecko id через /search - возвращает первую монету, у
    которой symbol совпадает с тикером (без учёта регистра). CoinGecko
    отдаёт результаты /search уже отсортированными по market cap, так
    что первое совпадение по symbol - почти всегда то, что нужно
    (старшая монета с таким тикером, а не случайный шиткоин-омоним)."""
    try:
        resp = requests.get(
            f"{_COINGECKO_URL}/search", params={"query": clean_ticker}, timeout=15
        )
        resp.raise_for_status()
        coins = resp.json().get("coins", [])
    except requests.RequestException as e:
        logger.warning("Не удалось найти CoinGecko id для %s: %s", clean_ticker, e)
        return None

    for coin in coins:
        if coin.get("symbol", "").upper() == clean_ticker:
            return coin["id"]

    # Точного совпадения по symbol нет - берём первый результат как
    # лучшее доступное приближение, если он вообще есть.
    return coins[0]["id"] if coins else None


def _get_coingecko_id(ticker: str) -> str | None:
    """Преобразует тикер в CoinGecko ID: сперва статичный маппинг
    топ-монет, потом кэш уже найденных раньше тикеров, и только если
    ничего нет - живой поиск через /search (с сохранением в кэш)."""
    clean_ticker = ticker.replace("USDT", "").upper()

    if clean_ticker in _TICKER_TO_COINGECKO:
        return _TICKER_TO_COINGECKO[clean_ticker]

    cached = queue_manager.get_cached_coingecko_id(clean_ticker)
    if cached:
        return cached

    found = _search_coingecko_id(clean_ticker)
    if found:
        queue_manager.set_cached_coingecko_id(clean_ticker, found)
        return found

    logger.warning("Не удалось определить CoinGecko id для тикера %s", clean_ticker)
    return None


def fetch_klines(symbol: str, days: int = 2) -> list:
    """Получает данные цены с CoinGecko за последние N дней."""
    coingecko_id = _get_coingecko_id(symbol)
    if coingecko_id is None:
        return []

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


def generate_chart_image(ticker: str, days: int = 2, expected_price: float | None = None) -> Path | None:
    """
    Возвращает путь к PNG с графиком цены тикера, либо None.

    expected_price - текущая цена тикера, посчитанная по данным Binance
    (signal.current_price). CoinGecko используется ТОЛЬКО для картинки,
    а тикеры на CoinGecko - не уникальны (несколько разных монет могут
    делить один и тот же symbol, см. _search_coingecko_id). Если найден
    "PHB" с CoinGecko - это не гарантия, что это тот же PHB, что торгуется
    на Binance. Поэтому при наличии expected_price сверяем последнюю цену
    из графика с реальной ценой сигнала: если они отличаются больше чем
    в MAX_PRICE_MISMATCH_RATIO раз - значит подтянулась чужая монета с
    совпавшим тикером, и публиковать такой график нельзя (числа на
    картинке не будут соответствовать числам в тексте поста).
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

    if expected_price is not None and expected_price > 0 and closes[-1] > 0:
        ratio = max(closes[-1], expected_price) / min(closes[-1], expected_price)
        if ratio > MAX_PRICE_MISMATCH_RATIO:
            logger.warning(
                "График %s отбракован: цена с CoinGecko (%.10g) отличается от цены сигнала "
                "с Binance (%.10g) в %.1fx - вероятно, CoinGecko вернул другую монету с "
                "совпадающим тикером. Публикация без графика.",
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