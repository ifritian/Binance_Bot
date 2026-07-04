"""
index_signal_scanner.py - RSI/Bollinger сигналы "удобно купить/продать"
только по монетам из Treasury Index (treasury_index.BASKET).

В отличие от scanner.py, который сканирует топ-150 ликвидных пар по
всему рынку без разбора, здесь вселенная - строго 15 монет собственного
индекса канала. Идея: подписчикам, которые следят конкретно за этой
корзиной, ценнее не "какая-то монета перепродана", а "SOL (Tier 1, 20%
индекса) сейчас в зоне перепроданности - удобный момент для докупки в
рамках управления корзиной", чем узнать об этом только постфактум в
еженедельной сводке Treasury Index. Это и есть "умный менеджмент по
индексу", о котором шла речь.

Переиспользует ВСЮ формулу RSI/Bollinger/дивергенции/score из scanner.py
(_build_signal) - тот же расчёт, что и в общем сканере, просто на
другой, гораздо более узкой вселенной тикеров, с тем же fallback-
механизмом смены тикера при ребрендинге (POL/MATIC), что и в
treasury_index.py.

Результат кладётся в ОТДЕЛЬНУЮ очередь (queue_manager.push_pending_index_signal),
не смешиваясь с общей очередью currency - у индекс-сигналов свой формат
поста и своё окно публикации (см. index_signal_generator.py, main.py).
"""
import logging

import requests

import queue_manager
import scanner
import treasury_index

logger = logging.getLogger(__name__)

_BASE_URL = "https://data-api.binance.vision/api/v3"


def _flatten_basket() -> list[dict]:
    coins = []
    for tier_key, tier_coins in treasury_index.BASKET.items():
        for c in tier_coins:
            coins.append({**c, "tier": tier_key, "tier_label": treasury_index.TIER_LABELS[tier_key]})
    return coins


def _fetch_quote_volumes() -> dict[str, float]:
    """Один bulk-запрос на объёмы всех пар сразу (эффективнее, чем 15
    отдельных запросов по одному на монету индекса)."""
    try:
        resp = requests.get(f"{_BASE_URL}/ticker/24hr", timeout=20)
        resp.raise_for_status()
        rows = resp.json()
    except requests.RequestException as e:
        logger.warning("Индекс-сканер: не удалось получить объёмы: %s", e)
        return {}

    result = {}
    for row in rows:
        try:
            result[row["symbol"]] = float(row["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
    return result


def _resolve_symbol_and_candles(coin: dict):
    """Пробует основной тикер, при неудаче - fallback (как в
    treasury_index._resolve_coin_change, но здесь нужны свечи для
    RSI/Bollinger, а не итоговый %). None, если ни один вариант не дал
    данных (монета временно недоступна и т.п.)."""
    primary_symbol = f"{coin['ticker']}USDT"
    candles = scanner._fetch_klines(primary_symbol)
    if candles:
        return primary_symbol, candles

    fallback = coin.get("fallback")
    if fallback:
        fb_symbol = f"{fallback}USDT"
        candles = scanner._fetch_klines(fb_symbol)
        if candles:
            logger.info("Индекс-сканер: %s недоступен, использую fallback %s", primary_symbol, fb_symbol)
            return fb_symbol, candles

    return None


def run_index_scan() -> int:
    """Сканирует только монеты Treasury Index, кладёт найденные сигналы
    в отдельную очередь. Возвращает число добавленных сигналов."""
    import config

    quote_volumes = _fetch_quote_volumes()
    added = 0

    for coin in _flatten_basket():
        resolved = _resolve_symbol_and_candles(coin)
        if resolved is None:
            continue
        symbol, candles = resolved
        quote_volume = quote_volumes.get(symbol, 0.0)

        signal = scanner._build_signal(symbol, candles, quote_volume)
        if signal is None:
            continue  # RSI сейчас не в экстремальной зоне - по этой монете сейчас нечего сказать

        direction_key = "short" if "перекуплен" in signal.direction else "long"
        # Отдельный namespace ("index:") в cooldown - чтобы совпадение
        # тикера с обычным сканером (SOL и там, и там) не мешало друг другу.
        if queue_manager.was_recently_alerted(f"index:{signal.ticker}", direction_key,
                                               config.INDEX_SIGNAL_ALERT_COOLDOWN_HOURS):
            continue

        if int(signal.score) <= config.MIN_INDEX_SIGNAL_SCORE_TO_PUBLISH:
            continue

        # Обогащаем описание контекстом индекса - пригодится генератору
        # поста, чтобы объяснить, почему это важно именно для управления
        # корзиной (тир и вес), а не просто "тикер перепродан".
        signal.description += f" [Индекс: {coin['tier_label']}, вес {coin['weight']:g}% корзины]"

        queue_manager.push_pending_index_signal(signal)
        queue_manager.mark_alerted(f"index:{signal.ticker}", direction_key)
        added += 1
        logger.info(
            "Индекс-сканер: новый сигнал %s %s (%s, score %s)",
            signal.ticker, signal.direction, coin["tier_label"], signal.score,
        )

    if added:
        logger.info("Индекс-сканер: добавлено %d новых сигналов", added)
    return added
