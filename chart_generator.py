"""
Генерация графика цены для тикера через CoinGecko API (без геоблокировки).
Поддерживает любые монеты через динамический кэш ID.
"""
import json
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
_CACHE_FILE = config.BASE_DIR / "coins_cache.json"

# Статический маппинг для топовых монет (ускорение)
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

# Кэш для динамически найденных монет
_COINS_CACHE = None


def _load_or_create_cache() -> dict:
    """Загружает кэш из файла или инициализирует пустой."""
    global _COINS_CACHE
    
    if _COINS_CACHE is not None:
        return _COINS_CACHE
    
    if _CACHE_FILE.exists():
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                _COINS_CACHE = json.load(f)
                logger.info("Загружен кэш монет (%d записей)", len(_COINS_CACHE))
                return _COINS_CACHE
        except Exception as e:
            logger.warning("Не удалось загрузить кэш: %s, начинаю с пустого", e)
    
    _COINS_CACHE = {}
    return _COINS_CACHE


def _save_cache() -> None:
    """Сохраняет кэш на диск."""
    if _COINS_CACHE is None:
        return
    
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_COINS_CACHE, f, indent=2, ensure_ascii=False)
        logger.debug("Кэш монет сохранён (%d записей)", len(_COINS_CACHE))
    except Exception as e:
        logger.warning("Не удалось сохранить кэш: %s", e)


def _search_coingecko_by_symbol(ticker: str) -> str | None:
    """Ищет монету на CoinGecko по тикеру через API поиска."""
    try:
        url = f"{_COINGECKO_URL}/search"
        params = {"query": ticker}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        # CoinGecko возвращает список coins с ID
        coins = data.get("coins", [])
        if coins:
            # Берём первый результат (обычно самый релевантный)
            coingecko_id = coins[0].get("id")
            if coingecko_id:
                logger.info("Найдена монета %s → %s через CoinGecko поиск", ticker, coingecko_id)
                return coingecko_id
    except requests.RequestException as e:
        logger.warning("Ошибка при поиске %s на CoinGecko: %s", ticker, e)
    
    return None


def _get_coingecko_id(ticker: str) -> str | None:
    """
    Преобразует тикер в CoinGecko ID:
    1. Проверяет статический маппинг
    2. Проверяет динамический кэш
    3. Ищет через CoinGecko API и сохраняет в кэш
    """
    clean_ticker = ticker.replace("USDT", "").upper()
    
    # 1️⃣ Проверяем статический маппинг
    if clean_ticker in _TICKER_TO_COINGECKO:
        return _TICKER_TO_COINGECKO[clean_ticker]
    
    # 2️⃣ Проверяем динамический кэш
    cache = _load_or_create_cache()
    if clean_ticker in cache:
        logger.debug("Найден %s в кэше → %s", clean_ticker, cache[clean_ticker])
        return cache[clean_ticker]
    
    # 3️⃣ Ищем через API и добавляем в кэш
    coingecko_id = _search_coingecko_by_symbol(clean_ticker)
    if coingecko_id:
        cache[clean_ticker] = coingecko_id
        _save_cache()
        return coingecko_id
    
    # Если не найдено - логируем, возвращаем None (не гадаем)
    logger.warning("Не найдена монета %s ни в маппинге, ни на CoinGecko", clean_ticker)
    return None


def fetch_klines(symbol: str, days: int = 2) -> list:
    """Получает данные цены с CoinGecko за последние N дней."""
    coingecko_id = _get_coingecko_id(symbol)
    
    if not coingecko_id:
        logger.warning("Не удалось определить CoinGecko ID для %s", symbol)
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
        logger.warning("Не удалось получить данные CoinGecko для %s (%s): %s", 
                      symbol, coingecko_id, e)
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