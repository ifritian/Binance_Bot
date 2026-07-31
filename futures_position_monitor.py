"""
futures_position_monitor.py - трейлинг-стоп для позиций, открытых
futures-ботом (см. futures_state.register_managed_position), плюс
обнаружение их закрытия (алерт владельцу через alerting.py). Вызывается
futures_loop.py КАЖДЫЙ цикл.

ТРЕЙЛИНГ-СТОП: дистанция трейлинга ФИКСИРОВАНА - |entry_price -
initial_stop_price|, сохраняется ОДИН РАЗ при открытии позиции (см.
futures_state.register_managed_position) и больше никогда не
пересчитывается заново от текущей цены. Если бы дистанция считалась
заново каждый цикл от текущей цены - она бы "плыла" вместе с ценой, и
это перестало бы быть трейлингом (стоп просто следовал бы за ценой на
исходном расстоянии, никогда не сокращая риск).

Стоп ТОЛЬКО подтягивается в выгодную сторону (выше для лонга, ниже для
шорта относительно текущего уровня стопа на бирже) - НИКОГДА не
отодвигается дальше от рынка, даже если цена откатила назад. Это и
есть определение трейлинг-стопа: цена может откатывать сколько угодно,
но однажды подтянутый стоп остаётся на месте (или подтягивается ещё
выше/ниже), а не отодвигается обратно.

Управляет ТОЛЬКО позициями из futures_state.list_managed_positions -
если позиция была открыта не через наш executor (например, вручную на
сайте биржи) и не зарегистрирована - трейлинг её не трогает.

Алерты о закрытии идут через alerting.send_owner_alert(state=futures_state) -
НЕ queue_manager (bot_state.db) - см. docstring futures_state.py про то,
почему состояние двух ботов разделено."""
import logging
from typing import Optional

import alerting
import futures_state
from futures_client import FuturesApiError
from futures_executor import ExecutionError, replace_stop_order

logger = logging.getLogger(__name__)

_LONG_SIDE, _SHORT_SIDE = "BUY", "SELL"


def _opposite_side(side: str) -> str:
    return _SHORT_SIDE if side == _LONG_SIDE else _LONG_SIDE


def compute_new_trailing_stop(side: str, trail_distance: float, mark_price: float,
                               current_stop_price: float) -> Optional[float]:
    """None, если трейлинг ничего не должен менять на этом цикле (цена
    не ушла настолько, чтобы новый уровень стопа стал ВЫГОДНЕЕ текущего).
    Иначе - новая (более выгодная) цена стопа."""
    if trail_distance <= 0:
        return None
    if side == _LONG_SIDE:
        candidate = mark_price - trail_distance
        return candidate if candidate > current_stop_price else None
    candidate = mark_price + trail_distance
    return candidate if candidate < current_stop_price else None


def _find_stop_algo_order(client, symbol: str) -> Optional[dict]:
    """Единственный активный STOP_MARKET algo-ордер по символу (не
    трогаем TAKE_PROFIT_MARKET - см. модульный docstring). None, если
    не найден (например, уже сработал, или его отменили вручную)."""
    orders = client.get_open_orders(symbol)
    stops = [o for o in orders if o.get("type") == "STOP_MARKET" and "algoId" in o]
    if not stops:
        return None
    if len(stops) > 1:
        logger.warning(
            "%s: найдено %d стоп-ордеров одновременно (ожидался 1) - беру первый; "
            "остальные, скорее всего, остаток прошлого цикла трейлинга (см. replace_stop_order)",
            symbol, len(stops),
        )
    return stops[0]


def manage_position(client, symbol: str, managed: dict, dry_run: bool = False) -> None:
    """Один цикл трейлинга для ОДНОЙ отслеживаемой позиции. Любая ошибка
    здесь должна логироваться и НЕ подниматься наружу - см.
    check_and_manage_all, которая иначе не сможет обработать остальные
    позиции в этом же цикле."""
    stop_order = _find_stop_algo_order(client, symbol)
    if stop_order is None:
        logger.warning(
            "%s: не найден активный стоп-ордер (уже сработал? отменён вручную?) - "
            "пропускаю трейлинг на этом цикле", symbol,
        )
        return

    current_stop_price = float(stop_order["triggerPrice"])
    try:
        mark_price = client.get_mark_price(symbol)
    except FuturesApiError as e:
        logger.error("%s: не удалось получить цену для трейлинга: %s", symbol, e)
        return

    new_stop = compute_new_trailing_stop(managed["side"], managed["trail_distance"], mark_price, current_stop_price)
    if new_stop is None:
        return  # цена не ушла достаточно - стоп остаётся как есть

    if dry_run:
        logger.info("DRY-RUN: %s - трейлинг-стоп подтянул бы %.6g -> %.6g (цена сейчас %.6g)",
                    symbol, current_stop_price, new_stop, mark_price)
        return

    close_side = _opposite_side(managed["side"])
    try:
        replace_stop_order(client, symbol, close_side, stop_order["algoId"], new_stop)
    except (FuturesApiError, ExecutionError) as e:
        logger.error("%s: не удалось подтянуть трейлинг-стоп до %.6g: %s", symbol, new_stop, e)
        return

    logger.info("%s: трейлинг-стоп подтянут %.6g -> %.6g (цена сейчас %.6g)",
                symbol, current_stop_price, new_stop, mark_price)


def _handle_closed_position(client, symbol: str, managed: dict) -> None:
    pnl_note = ""
    try:
        income_rows = client.get_income_history(income_type="REALIZED_PNL", limit=10)
        matching = [r for r in income_rows if r.get("symbol") == symbol]
        if matching:
            pnl = sum(float(r["income"]) for r in matching)
            pnl_note = f", примерный результат {pnl:+.4f} USDT"
    except FuturesApiError:
        pass  # алерт всё равно важнее точного числа - шлём и без него

    message = f"Позиция {symbol} ({managed['side']}) закрылась{pnl_note} - убрана из отслеживания трейлинг-стопа."
    logger.info(message)
    try:
        alerting.send_owner_alert(f"futures_position_closed:{symbol}", message, state=futures_state)
    except Exception:
        logger.exception("Не удалось отправить алерт о закрытии %s", symbol)

    futures_state.unregister_managed_position(symbol)


def check_and_manage_all(client, dry_run: bool = False) -> None:
    """Вызывается КАЖДЫЙ цикл futures_loop.py: трейлит стопы всех
    отслеживаемых позиций + обнаруживает закрытие (была в реестре,
    больше не открыта на бирже) - шлёт алерт и убирает из реестра.
    Ошибка на ОДНОЙ позиции не должна мешать обработке остальных."""
    managed_positions = futures_state.list_managed_positions()
    if not managed_positions:
        return

    open_symbols = {p["symbol"] for p in client.get_all_positions()}

    for symbol, managed in list(managed_positions.items()):
        if symbol not in open_symbols:
            try:
                _handle_closed_position(client, symbol, managed)
            except Exception:
                logger.exception("%s: сбой при обработке закрытия - продолжаю с остальными позициями", symbol)
            continue
        try:
            manage_position(client, symbol, managed, dry_run=dry_run)
        except Exception:
            logger.exception("%s: сбой при трейлинге - продолжаю с остальными позициями", symbol)
