#!/usr/bin/env python3
"""
futures_loop.py - НЕПРЕРЫВНО работающий futures-бот: каждый цикл (по
умолчанию раз в минуту, см. config.BINANCE_FUTURES_LOOP_INTERVAL_SECONDS)

1. Подтягивает трейлинг-стоп на всех уже открытых (ботом) позициях и
   обнаруживает закрытие любой из них (алерт владельцу) - см.
   futures_position_monitor.py.
2. Если сейчас открыто МЕНЬШЕ позиций, чем разрешает risk_guard
   (config.BINANCE_FUTURES_MAX_OPEN_POSITIONS) - сканирует рынок в
   поисках новой сделки (см. futures_auto_trade.run_cycle). Если мест
   нет - сканирование рынка на этом цикле ПРОПУСКАЕТСЯ целиком (не
   тратим впустую ~150 запросов к Binance на пары каждую минуту, если
   всё равно некуда открывать) - следующий полноценный скан произойдёт
   сразу же, как только одна из позиций закроется и появится свободное
   место.

Работает, пока не остановишь Ctrl+C (или пока процесс не убьют иначе) -
это ОБЫЧНЫЙ бесконечный цикл в терминале/консоли, не демон и не служба.
Если нужно, чтобы он переживал закрытие терминала - заверни в screen/
tmux/systemd (или аналог на Windows, например Task Scheduler + start
/min) - это уже вопрос ОС, не этого скрипта.

ТЕ ЖЕ СЛОИ БЕЗОПАСНОСТИ, что и у futures_auto_trade.py (см. его
docstring) - ТОЛЬКО TESTNET, dry-run по умолчанию (--live нужен явно),
risk_guard.py, полностью отдельное состояние от постинг-бота
(futures_state.py). Трейлинг-стоп ТОЖЕ уважает --live/dry-run - без
--live он только логирует, что подтянул бы стоп, ничего не меняя на
бирже.

Использование:
    export BINANCE_FUTURES_API_KEY=...
    export BINANCE_FUTURES_API_SECRET=...
    python3 futures_loop.py                # dry-run - ничего не откроет и не подтянет
    python3 futures_loop.py --live         # реально торгует и трейлит на testnet
"""
import argparse
import logging
import os
import sys
import time

import config
import futures_position_monitor
import risk_guard
from futures_auto_trade import run_cycle
from futures_client import FuturesClient, TESTNET_BASE_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("futures_loop")


def run_one_iteration(client, risk_limits: risk_guard.RiskLimits, live: bool) -> None:
    """Один проход цикла - вынесен отдельной функцией, чтобы его можно
    было протестировать без реального time.sleep между итерациями."""
    futures_position_monitor.check_and_manage_all(client, dry_run=not live)

    open_count = len(client.get_all_positions())
    if open_count >= risk_limits.max_open_positions:
        logger.info(
            "Открыто %d/%d позиций - все места заняты, пропускаю скан рынка на этом цикле "
            "(проверю трейлинг снова через %ds)",
            open_count, risk_limits.max_open_positions, config.BINANCE_FUTURES_LOOP_INTERVAL_SECONDS,
        )
        return

    stats = run_cycle(client, risk_limits, live=live)
    if stats["executed"]:
        for result in stats["executed"]:
            logger.info("Открыта новая позиция: %s %s qty=%.8g вход~%.6g стоп=%.6g тейк=%.6g",
                        result.symbol, result.side, result.quantity,
                        result.entry_price, result.stop_price, result.take_profit_price)
    elif not live and stats["skipped_dry_run"]:
        logger.info("DRY-RUN: %d сигнал(ов) прошли бы к исполнению на этом цикле", len(stats["skipped_dry_run"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                        help="Реально открывать позиции и подтягивать трейлинг-стоп на testnet. "
                             "Без этого флага - dry-run: только показывает, что было бы сделано.")
    args = parser.parse_args()

    api_key = os.environ.get("BINANCE_FUTURES_API_KEY", "")
    api_secret = os.environ.get("BINANCE_FUTURES_API_SECRET", "")
    if not api_key or not api_secret:
        logger.error(
            "Не заданы BINANCE_FUTURES_API_KEY/BINANCE_FUTURES_API_SECRET (testnet-ключи, "
            "см. https://testnet.binancefuture.com) - выставь через export, не хардкодь в файл."
        )
        return 1

    # Жёстко TESTNET_BASE_URL - см. docstring модуля.
    client = FuturesClient(api_key=api_key, api_secret=api_secret, base_url=TESTNET_BASE_URL)
    risk_limits = risk_guard.limits_from_config(config)
    interval = config.BINANCE_FUTURES_LOOP_INTERVAL_SECONDS

    if not args.live:
        logger.info("=== DRY-RUN (без --live) - ни одна позиция не будет открыта/изменена ===")
    logger.info("futures_loop: старт, цикл каждые %ds, максимум %d открытых позиций одновременно "
                "(Ctrl+C для остановки)", interval, risk_limits.max_open_positions)

    try:
        while True:
            try:
                run_one_iteration(client, risk_limits, live=args.live)
            except Exception:
                # Один неудачный цикл (сетевой сбой, временная ошибка биржи и
                # т.п.) не должен останавливать бота насовсем - следующий цикл
                # попробует снова. Если ошибка систематическая (например,
                # неверные ключи) - будет видно по повторяющимся логам.
                logger.exception("futures_loop: ошибка в цикле - жду следующего цикла")
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("futures_loop: остановлен (Ctrl+C)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
