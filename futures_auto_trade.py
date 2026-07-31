#!/usr/bin/env python3
"""
futures_auto_trade.py - запускает scanner.run_scan() и АВТОМАТИЧЕСКИ (без
подтверждения на каждую сделку) открывает защищённые позиции по сигналам,
которые проходят все фильтры (см. futures_signal_bridge.execute_signal и
risk_guard.py). Это то, ради чего затевался весь проект - но и самый
рискованный компонент в нём (см. docstring futures_signal_bridge.py).

ТРИ НЕЗАВИСИМЫХ СЛОЯ БЕЗОПАСНОСТИ, каждый работает независимо от
остальных:
1. ТОЛЬКО TESTNET - жёстко используется futures_client.TESTNET_BASE_URL
   напрямую (та же логика, что и в futures_testnet_demo.py) - ни один
   параметр окружения/config.py не может перевести ЭТОТ скрипт на
   реальный счёт. Если когда-нибудь захочется автоматической торговли на
   mainnet - это ОТДЕЛЬНОЕ осознанное решение, не следствие того, что
   кто-то забыл переменную окружения.
2. DRY-RUN ПО УМОЛЧАНИЮ - без флага --live скрипт только ПОКАЗЫВАЕТ, что
   было бы сделано (какие сигналы прошли фильтры), но не открывает
   НИЧЕГО - ни одного вызова на изменение состояния биржи. --live нужно
   передать явно.
3. risk_guard.py (см. config.BINANCE_FUTURES_MAX_*) - максимум открытых
   позиций, дневной лимит убытка, серия убытков подряд - те же самые
   предохранители, что и у ручного входа через futures_testnet_demo.py.
4. ПОЛНОСТЬЮ ОТДЕЛЬНОЕ СОСТОЯНИЕ от постинг-бота (см. futures_state.py) -
   этот скрипт вызывает scanner.run_scan(state=futures_state), поэтому
   ни kill switch/baseline (risk_guard.py), ни cooldown сканирования
   рынка НИКОГДА не пишутся в bot_state.db (тот коммитит и пушит сам
   постинг-бот через GitHub Actions - раньше это создавало конфликты
   git, если оба бота писали в один и тот же файл). Значит и очередь
   постов (queue_manager.push_pending_signal) сюда НЕ попадает -
   futures-бот не публикует посты, только торгует.

Использование:
    export BINANCE_FUTURES_API_KEY=...      # testnet-ключ
    export BINANCE_FUTURES_API_SECRET=...   # testnet-секрет
    python3 futures_auto_trade.py                # dry-run - ничего не откроет
    python3 futures_auto_trade.py --live          # реально откроет позиции на testnet

Предполагается ручной периодический запуск (как и futures_testnet_demo.py) -
скрипт НЕ добавлен в .github/workflows/bot.yml. Подключение к
расписанию (GitHub Actions cron) - отдельное решение, которое стоит
принимать осознанно, а не бандлом с этим изменением.
"""
import argparse
import logging
import os
import sys

import config
import futures_signal_bridge
import futures_state
import risk_guard
import scanner
from futures_client import FuturesClient, TESTNET_BASE_URL, FuturesApiError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("futures_auto_trade")


def run_cycle(client, risk_limits: risk_guard.RiskLimits, live: bool) -> dict:
    """Один цикл: скан рынка -> попытка исполнить сигналы, прошедшие
    фильтры. Общая логика для одноразового запуска (main() ниже) и
    futures_loop.py (тот же цикл, но по кругу). Возвращает
    {"signals_accepted": int, "executed": [ProtectedPositionResult, ...],
    "skipped_dry_run": [RsiSignal, ...]}."""
    executed = []
    skipped_dry_run = []

    def _on_signal_accepted(signal, symbol):
        if not live:
            skipped_dry_run.append(signal)
            logger.info("DRY-RUN: сигнал %s %s (%s, score %s) прошёл бы к исполнению "
                        "(запусти с --live, чтобы реально открыть)",
                        signal.ticker, signal.direction, signal.strategy, signal.score)
            return
        result = futures_signal_bridge.execute_signal(
            client, signal,
            risk_pct=config.BINANCE_FUTURES_RISK_PCT_PER_TRADE,
            leverage=config.BINANCE_FUTURES_DEFAULT_LEVERAGE,
            risk_limits=risk_limits,
            min_score=config.BINANCE_FUTURES_MIN_SIGNAL_SCORE,
        )
        if result is not None:
            executed.append(result)

    signals_accepted = scanner.run_scan(on_signal_accepted=_on_signal_accepted, state=futures_state)
    return {"signals_accepted": signals_accepted, "executed": executed, "skipped_dry_run": skipped_dry_run}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                        help="Реально открывать позиции на testnet. Без этого флага - dry-run: "
                             "показывает, какие сигналы прошли бы фильтры, ничего не открывая.")
    args = parser.parse_args()

    api_key = os.environ.get("BINANCE_FUTURES_API_KEY", "")
    api_secret = os.environ.get("BINANCE_FUTURES_API_SECRET", "")
    if not api_key or not api_secret:
        logger.error(
            "Не заданы BINANCE_FUTURES_API_KEY/BINANCE_FUTURES_API_SECRET (testnet-ключи, "
            "см. https://testnet.binancefuture.com) - выставь через export, не хардкодь в файл."
        )
        return 1

    # Жёстко TESTNET_BASE_URL - см. docstring модуля, пункт 1.
    client = FuturesClient(api_key=api_key, api_secret=api_secret, base_url=TESTNET_BASE_URL)
    risk_limits = risk_guard.limits_from_config(config)

    if not args.live:
        logger.info("=== DRY-RUN (без --live) - ни одна позиция не будет открыта ===")

    stats = run_cycle(client, risk_limits, live=args.live)
    executed, skipped_dry_run = stats["executed"], stats["skipped_dry_run"]

    logger.info(
        "Готово: %d сигнал(ов) прошли фильтры сканера, %d позици(й) реально открыто%s",
        stats["signals_accepted"], len(executed),
        f", {len(skipped_dry_run)} прошли бы фильтр (dry-run)" if not args.live else "",
    )
    for result in executed:
        logger.info("  -> %s %s qty=%.8g вход~%.6g стоп=%.6g тейк=%.6g",
                    result.symbol, result.side, result.quantity,
                    result.entry_price, result.stop_price, result.take_profit_price)

    if args.live and executed:
        logger.info("Проверь позиции на https://testnet.binancefuture.com")
    return 0


if __name__ == "__main__":
    sys.exit(main())
