#!/usr/bin/env python3
"""
futures_position_monitor.py - следит за позициями, открытыми
futures_signal_bridge.execute_signal (см. queue_manager.
get_open_futures_positions), и уведомляет владельца в Telegram, когда
позиция закрывается - по стопу, по тейку, или как-то иначе.

Зачем это отдельный шаг, а не часть futures_auto_trade.py: открытие
новых позиций и слежение за уже открытыми - разные по риску операции с
разными последствиями отказа. У этой ещё есть побочная обязанность -
чистить "осиротевший" условный ордер: Binance Futures НЕ отменяет пару
STOP_MARKET/TAKE_PROFIT_MARKET автоматически (это не OCO), так что
после срабатывания одного из них второй остаётся висеть сам по себе.
Без чистки: (а) позиция по этому символу не сможет переоткрыться,
пока get_position видит что-то по нему связанное, и (б) оставшийся
ордер рано или поздно исполнится сам по себе, если цена туда вернётся,
уже без всякой связи с исходным сигналом.

Как определяется причина закрытия:
1. Позиции больше нет на бирже (client.get_position -> None или
   positionAmt == 0) - значит закрылась (по стопу, тейку, ликвидации
   или вручную с сайта/CLI).
2. Смотрим, какой из двух условных ордеров (stop_order_id /
   take_profit_order_id, сохранённых futures_signal_bridge при входе)
   всё ещё висит в open orders по символу - тот, что НЕ висит,
   сработал и закрыл позицию. Если висит один из двух - отменяем
   оставшийся (см. выше). Если ни один не висит - позиция закрыта
   как-то иначе (вручную, ликвидация) - помечаем "неизвестно".
3. Реальный PnL берём из client.get_income_history (incomeType=
   REALIZED_PNL) за период с момента открытия - это фактическое число
   с биржи (уже с учётом комиссий и проскальзывания), а не оценка по
   цене входа/выхода.

Использование (те же переменные окружения, что и futures_auto_trade.py):
    export BINANCE_FUTURES_API_KEY=...
    export BINANCE_FUTURES_API_SECRET=...
    python3 futures_position_monitor.py
"""
import logging
import os
import sys
import time

import alerting
from futures_client import FuturesApiError, FuturesClient, TESTNET_BASE_URL
import queue_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("futures_position_monitor")


def _realized_pnl_since(client, symbol: str, since_ts: float) -> float:
    """Сумма REALIZED_PNL по символу с момента since_ts (unix-секунды).
    get_income_history отдаёт время в мс - тот же формат, что уже
    использует risk_guard._consecutive_losses."""
    since_ms = int(since_ts * 1000)
    rows = client.get_income_history(income_type="REALIZED_PNL", limit=200)
    return sum(
        float(r.get("income", 0)) for r in rows
        if r.get("symbol") == symbol and int(r.get("time", 0)) >= since_ms
    )


def _determine_close_reason_and_cleanup(client, record: dict, symbol: str) -> str:
    """Возвращает человекочитаемую причину закрытия и попутно отменяет
    "осиротевший" условный ордер, если он остался висеть (см. docstring
    модуля)."""
    try:
        open_orders = client.get_open_orders(symbol)
    except FuturesApiError as e:
        logger.warning(
            "futures_position_monitor: не удалось получить open orders для %s: %s - причина закрытия неизвестна",
            symbol, e,
        )
        return "неизвестно (ошибка API при проверке ордеров)"

    open_order_ids = {o.get("orderId") for o in open_orders}
    stop_still_open = record.get("stop_order_id") in open_order_ids
    tp_still_open = record.get("take_profit_order_id") in open_order_ids

    if stop_still_open and not tp_still_open:
        reason = "тейк-профит (TP)"
    elif tp_still_open and not stop_still_open:
        reason = "стоп-лосс (SL)"
    elif not stop_still_open and not tp_still_open:
        reason = "неизвестно (оба условных ордера уже неактивны - возможно, закрыта вручную)"
    else:
        reason = "неизвестно (оба условных ордера всё ещё формально активны)"

    if stop_still_open or tp_still_open:
        try:
            client.cancel_all_open_orders(symbol)
            logger.info("futures_position_monitor: отменён оставшийся условный ордер по %s", symbol)
        except FuturesApiError as e:
            logger.warning("futures_position_monitor: не удалось отменить оставшийся ордер по %s: %s", symbol, e)

    return reason


def check_open_positions(client) -> dict:
    """Проходит по всем отслеживаемым позициям (queue_manager.
    get_open_futures_positions), для каждой проверяет, закрылась ли она
    на бирже, и если да - шлёт уведомление владельцу в Telegram и
    переносит запись в closed_futures_positions. Возвращает сводку
    {"still_open": N, "closed": N}. Сбой на ОДНОЙ позиции (сетевая
    ошибка и т.п.) не прерывает проверку остальных - позиция просто
    остаётся в трекинге до следующего запуска."""
    tracked = queue_manager.get_open_futures_positions()
    if not tracked:
        return {"still_open": 0, "closed": 0}

    still_open = []
    newly_closed = []

    for record in tracked:
        symbol = record.get("symbol", "")
        try:
            position = client.get_position(symbol)
        except FuturesApiError as e:
            logger.warning(
                "futures_position_monitor: не удалось проверить позицию %s: %s - оставляю в трекинге до следующего раза",
                symbol, e,
            )
            still_open.append(record)
            continue

        position_amt = float(position["positionAmt"]) if position else 0.0
        if position_amt != 0:
            still_open.append(record)
            continue

        reason = _determine_close_reason_and_cleanup(client, record, symbol)
        pnl = _realized_pnl_since(client, symbol, record.get("opened_at", 0))

        closed_record = dict(record, closed_at=time.time(), close_reason=reason, realized_pnl=pnl)
        newly_closed.append(closed_record)

        entry = record.get("entry_price", 0) or 0
        quantity = record.get("quantity", 0) or 0
        notional = entry * quantity
        pnl_pct = (pnl / notional * 100) if notional else 0.0
        emoji = "\U0001F7E2" if pnl > 0 else ("\U0001F534" if pnl < 0 else "\u26AA")

        message = (
            f"{emoji} Позиция закрыта: {record.get('ticker', symbol)} {record.get('direction', '')}\n"
            f"Причина: {reason}\n"
            f"Вход: {entry:.6g}  Кол-во: {quantity:.8g}\n"
            f"Реализованный PnL: {pnl:+.4f} USDT ({pnl_pct:+.2f}% от размера позиции)\n"
            f"Стратегия: {record.get('strategy', '?')} (score {record.get('score', '?')})"
        )
        alerting.send_owner_alert(
            f"futures_position_closed:{symbol}:{record.get('opened_at', 0)}",
            message,
            min_repeat_hours=0,  # ключ уникален на конкретную позицию - троттлить тут нечего
        )
        logger.info("futures_position_monitor: %s закрыта (%s), PnL %.4f USDT", symbol, reason, pnl)

    queue_manager.replace_open_futures_positions(still_open)
    if newly_closed:
        queue_manager.append_closed_futures_positions(newly_closed)

    return {"still_open": len(still_open), "closed": len(newly_closed)}


def main() -> int:
    api_key = os.environ.get("BINANCE_FUTURES_API_KEY", "")
    api_secret = os.environ.get("BINANCE_FUTURES_API_SECRET", "")
    if not api_key or not api_secret:
        logger.error(
            "Не заданы BINANCE_FUTURES_API_KEY/BINANCE_FUTURES_API_SECRET (testnet-ключи, "
            "см. https://testnet.binancefuture.com) - выставь через export, не хардкодь в файл."
        )
        return 1

    client = FuturesClient(api_key=api_key, api_secret=api_secret, base_url=TESTNET_BASE_URL)
    summary = check_open_positions(client)
    logger.info(
        "Готово: %d позиций всё ещё открыто, %d закрыто и обработано в этом прогоне",
        summary["still_open"], summary["closed"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
