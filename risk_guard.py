"""
risk_guard.py - общие предохранители ПОВЕРХ риска отдельной сделки (см.
config.BINANCE_FUTURES_RISK_PCT_PER_TRADE и futures_executor.
calc_position_size). Каждая позиция по отдельности может быть правильно
посчитана по риску - и всё равно ничто не мешает открыть их сколько
угодно подряд, пока не кончится баланс, или поймать серию убытков,
каждый из которых сам по себе был "в пределах риска". Этот модуль -
именно про СУММАРНУЮ картину, а не про отдельную сделку:

1. Максимум ОДНОВРЕМЕННО открытых позиций (across всех символов).
2. Дневной лимит убытка в % от баланса на начало UTC-дня.
3. Серия убыточных сделок ПОДРЯД (по факту закрытия на бирже).

Лимиты 2 и 3 при срабатывании ВЗВОДЯТ kill switch (см.
queue_manager.set_kill_switch) - персистентный (bot_state.db) флаг
"торговля остановлена", который НЕ снимается сам по себе - ни на
следующий UTC-день, ни при следующей прибыльной сделке. Снять его можно
только осознанно: `python3 risk_guard_cli.py reset`, посмотрев вначале,
что случилось (`risk_guard_cli.py status`). Это НАМЕРЕННО консервативнее
"тихого" автовосстановления - если один из этих двух лимитов сработал,
решение продолжать торговать должно быть решением человека, а не
побочным эффектом того, что цифры на бирже сами вернулись в норму.

Лимит 1 (открытых позиций) - НЕ взводит kill switch: это не "что-то
пошло не так", а просто "подожди, пока освободится слот" - само
разрешится, когда одна из открытых позиций закроется.

futures_executor.open_protected_position вызывает
check_new_position_allowed ПЕРВЫМ делом - до единого API-вызова на
ИЗМЕНЕНИЕ чего-либо на бирже (до set_leverage и дальше). Отказ здесь
гарантирует, что позиция вообще не будет открыта - не "открыта и потом
аварийно закрыта".
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import queue_manager

logger = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    max_open_positions: int
    max_daily_loss_pct: float
    max_consecutive_losses: int


def limits_from_config(config) -> RiskLimits:
    return RiskLimits(
        max_open_positions=config.BINANCE_FUTURES_MAX_OPEN_POSITIONS,
        max_daily_loss_pct=config.BINANCE_FUTURES_MAX_DAILY_LOSS_PCT,
        max_consecutive_losses=config.BINANCE_FUTURES_MAX_CONSECUTIVE_LOSSES,
    )


def _utc_day_key(ts: Optional[float] = None) -> str:
    dt = datetime.now(timezone.utc) if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _daily_loss_pct(client, asset: str = "USDT") -> tuple[float, float, float]:
    """Возвращает (loss_pct, baseline, current). loss_pct положительный,
    если баланс УМЕНЬШИЛСЯ относительно baseline (0 или отрицательный,
    если баланс не падал/вырос за сегодня). baseline фиксируется РОВНО
    ОДИН РАЗ за UTC-день - при самой первой проверке (см. docstring
    модуля) - и дальше не пересчитывается до следующего дня, даже если
    эту функцию вызвать снова позже в тот же день."""
    day_key = _utc_day_key()
    baseline = queue_manager.get_risk_daily_baseline(day_key)
    current = client.get_wallet_balance(asset)
    if baseline is None:
        baseline = current
        queue_manager.set_risk_daily_baseline(day_key, baseline)
        logger.info("risk_guard: зафиксирован дневной baseline на %s: %.4f %s", day_key, baseline, asset)
    if baseline <= 0:
        return 0.0, baseline, current
    loss_pct = (baseline - current) / baseline * 100
    return loss_pct, baseline, current


def _consecutive_losses(client, lookback: int = 50) -> int:
    """Считает убыточные сделки ПОДРЯД, начиная с самой последней
    закрытой - по истории income (incomeType=REALIZED_PNL), не по
    локальному логу бота (тот не увидит сделку, закрытую вручную на
    сайте биржи). Записи с income == 0 (например, чисто комиссийные
    строки без реального закрытия позиции) игнорируются - это не
    "выигрыш" и не "проигрыш". Сортирует по времени сам, не полагаясь
    на порядок, в котором Binance отдаёт список."""
    rows = client.get_income_history(income_type="REALIZED_PNL", limit=lookback)
    trades = [
        float(r["income"]) for r in sorted(rows, key=lambda r: int(r.get("time", 0)))
        if float(r.get("income", 0)) != 0
    ]
    streak = 0
    for pnl in reversed(trades):
        if pnl < 0:
            streak += 1
        else:
            break
    return streak


def check_new_position_allowed(client, limits: RiskLimits) -> Optional[str]:
    """None - можно открывать новую позицию. Иначе - строка с причиной
    отказа. Намеренно НЕ бросает исключение сама - futures_executor
    оборачивает результат в ExecutionError на своей стороне,
    единообразно с остальными отказами до входа (недостаточный баланс
    и т.п.)."""
    kill_switch = queue_manager.get_kill_switch()
    if kill_switch is not None:
        return (
            f"KILL SWITCH ВЗВЕДЁН ({kill_switch['reason']}) - новые позиции заблокированы, "
            "пока кто-то осознанно не снимет его (python3 risk_guard_cli.py reset)."
        )

    open_positions = client.get_all_positions()
    if len(open_positions) >= limits.max_open_positions:
        symbols = ", ".join(p.get("symbol", "?") for p in open_positions)
        return (
            f"уже открыто {len(open_positions)}/{limits.max_open_positions} позиций ({symbols}) - "
            "новая позиция не откроется, пока одна из текущих не закроется"
        )

    loss_pct, baseline, current = _daily_loss_pct(client)
    if loss_pct >= limits.max_daily_loss_pct:
        reason = (
            f"дневной убыток {loss_pct:.2f}% >= лимита {limits.max_daily_loss_pct:.2f}% "
            f"(baseline {baseline:.2f} -> сейчас {current:.2f})"
        )
        queue_manager.set_kill_switch(reason)
        logger.error("risk_guard: KILL SWITCH ВЗВЕДЁН (дневной лимит убытка): %s", reason)
        return f"KILL SWITCH ВЗВЕДЁН ({reason}) - новые позиции заблокированы, пока кто-то осознанно не снимет его."

    streak = _consecutive_losses(client, lookback=max(limits.max_consecutive_losses * 5, 20))
    if streak >= limits.max_consecutive_losses:
        reason = f"{streak} убыточных сделок подряд (лимит {limits.max_consecutive_losses})"
        queue_manager.set_kill_switch(reason)
        logger.error("risk_guard: KILL SWITCH ВЗВЕДЁН (серия убытков подряд): %s", reason)
        return f"KILL SWITCH ВЗВЕДЁН ({reason}) - новые позиции заблокированы, пока кто-то осознанно не снимет его."

    return None


def status(client, limits: RiskLimits) -> dict:
    """Снимок текущего состояния для risk_guard_cli.py status /
    диагностики. В отличие от check_new_position_allowed, сама НИКОГДА
    не взводит kill switch по превышенным лимитам - только сообщает о
    нём, если он уже взведён. Побочный эффект: если сегодня ещё не было
    ни одной проверки, зафиксирует дневной baseline (та же логика, что
    и при обычной проверке - baseline должен быть один и тот же,
    откуда бы его ни зафиксировали первым)."""
    kill_switch = queue_manager.get_kill_switch()
    open_positions = client.get_all_positions()
    loss_pct, baseline, current = _daily_loss_pct(client)
    streak = _consecutive_losses(client, lookback=max(limits.max_consecutive_losses * 5, 20))
    return {
        "kill_switch": kill_switch,
        "open_positions": len(open_positions),
        "open_positions_symbols": [p.get("symbol") for p in open_positions],
        "max_open_positions": limits.max_open_positions,
        "daily_loss_pct": round(loss_pct, 3),
        "daily_baseline": baseline,
        "daily_current": current,
        "max_daily_loss_pct": limits.max_daily_loss_pct,
        "consecutive_losses": streak,
        "max_consecutive_losses": limits.max_consecutive_losses,
    }
