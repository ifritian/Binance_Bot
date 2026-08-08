"""
risk_guard.py - общие предохранители ПОВЕРХ риска отдельной сделки (см.
config.BINANCE_FUTURES_RISK_PCT_PER_TRADE и futures_executor.
calc_position_size). Каждая позиция по отдельности может быть правильно
посчитана по риску - и всё равно ничто не мешает открыть их сколько
угодно подряд, пока не кончится баланс, или поймать серию убытков,
каждый из которых сам по себе был "в пределах риска". Этот модуль -
именно про СУММАРНУЮ картину, а не про отдельную сделку:

1. Максимум ОДНОВРЕМЕННО открытых позиций (across всех символов).
2. Максимум позиций В ОДНУ СТОРОНУ одновременно (лонг или шорт
   отдельно) - пункт 1 сам по себе не мешает набрать, например, лонг по
   BTC+ETH+SOL сразу - формально три разных слота, а по факту одна
   большая ставка на рынок вверх, а не три независимых позиции. См.
   get_risk_multiplier ниже про пункт 4 - это НЕ то же самое: там про
   размер риска одной сделки после серии убытков, а не про то, сколько
   сделок можно набрать в одну сторону.
3. Дневной лимит убытка в % от баланса на начало UTC-дня.
4. Серия убыточных сделок ПОДРЯД (по факту закрытия на бирже).
5. Мягкое снижение риска НОВОЙ сделки (см. get_risk_multiplier), ещё
   ДО того, как серия убытков дойдёт до порога пункта 4 и остановит
   торговлю целиком - промежуточная ступень, а не замена жёсткому
   выключателю.

Лимиты 3 и 4 при срабатывании ВЗВОДЯТ kill switch (см.
queue_manager.set_kill_switch) - персистентный (bot_state.db) флаг
"торговля остановлена", который НЕ снимается сам по себе - ни на
следующий UTC-день, ни при следующей прибыльной сделке. Снять его можно
только осознанно: `python3 risk_guard_cli.py reset`, посмотрев вначале,
что случилось (`risk_guard_cli.py status`). Это НАМЕРЕННО консервативнее
"тихого" автовосстановления - если один из этих двух лимитов сработал,
решение продолжать торговать должно быть решением человека, а не
побочным эффектом того, что цифры на бирже сами вернулись в норму.

Лимиты 1, 2 и пункт 5 (мягкое снижение риска) - НЕ взводят kill switch:
это не "что-то пошло не так", а штатная адаптация (подожди слот / рискуй
меньше, пока не восстановишься) - само разрешится на следующей успешной
сделке или освободившемся слоте.

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
    # Мягкая ступень де-рискования ДО жёсткого kill switch - см.
    # get_risk_multiplier() ниже и docstring config.BINANCE_FUTURES_SOFT_DERISK_*.
    # Дефолты здесь совпадают с config.py и существуют только чтобы не
    # ломать старые вызовы RiskLimits(...) без этих двух аргументов
    # (тесты, старые вызывающие места) - в реальной работе бота их
    # всегда явно задаёт limits_from_config.
    soft_derisk_after_losses: int = 2
    soft_derisk_multiplier: float = 0.5
    # A4: лимит позиций В ОДНУ СТОРОНУ одновременно (см. модульный
    # docstring, пункт 2) - None означает "не проверять" (полностью
    # выключено), а не "0 разрешено". Дефолт None, а не число - чтобы
    # старый код/тесты, которые создают RiskLimits(...) без этого поля,
    # не начали внезапно ловить отказ по лимиту, который они не просили.
    max_same_direction_positions: Optional[int] = None


def limits_from_config(config) -> RiskLimits:
    return RiskLimits(
        max_open_positions=config.BINANCE_FUTURES_MAX_OPEN_POSITIONS,
        max_daily_loss_pct=config.BINANCE_FUTURES_MAX_DAILY_LOSS_PCT,
        max_consecutive_losses=config.BINANCE_FUTURES_MAX_CONSECUTIVE_LOSSES,
        soft_derisk_after_losses=config.BINANCE_FUTURES_SOFT_DERISK_AFTER_LOSSES,
        soft_derisk_multiplier=config.BINANCE_FUTURES_SOFT_DERISK_MULTIPLIER,
        max_same_direction_positions=config.BINANCE_FUTURES_MAX_SAME_DIRECTION_POSITIONS,
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


def _group_partial_fills(rows: list) -> list:
    """Одно закрытие ОДНОЙ позиции Binance нередко исполняет несколькими
    частичными филлами (partial fills) - каждый филл прилетает в income
    history отдельной строкой REALIZED_PNL, но с ОДИНАКОВЫМ symbol и
    ОДИНАКОВЫМ timestamp (совпадает даже до миллисекунды). Без этой
    группировки один реальный убыточный трейд, закрытый, скажем, 27
    филлами, засчитывался бы как 27 отдельных убытков подряд - именно
    так на практике серия "3 убытка подряд" ошибочно раздувалась до
    20-30+ и не давала снять kill switch. rows должны быть уже
    отсортированы по времени - функция сохраняет этот порядок группами
    (группа встаёт на место своего первого филла)."""
    grouped: dict[tuple, float] = {}
    order: list[tuple] = []
    for r in rows:
        key = (r.get("symbol"), int(r.get("time", 0)))
        if key not in grouped:
            grouped[key] = 0.0
            order.append(key)
        grouped[key] += float(r.get("income", 0))
    return [grouped[k] for k in order]


def _consecutive_losses(client, lookback: int = 50) -> int:
    """Считает убыточные СДЕЛКИ (не строки income - см. _group_partial_fills)
    ПОДРЯД, начиная с самой последней закрытой - по истории income
    (incomeType=REALIZED_PNL), не по локальному логу бота (тот не увидит
    сделку, закрытую вручную на сайте биржи). Записи с income == 0
    (например, чисто комиссийные строки без реального закрытия позиции)
    игнорируются - это не "выигрыш" и не "проигрыш". Сортирует по
    времени сам, не полагаясь на порядок, в котором Binance отдаёт
    список."""
    rows = client.get_income_history(income_type="REALIZED_PNL", limit=lookback)
    nonzero_sorted = [
        r for r in sorted(rows, key=lambda r: int(r.get("time", 0)))
        if float(r.get("income", 0)) != 0
    ]
    trades = _group_partial_fills(nonzero_sorted)
    streak = 0
    for pnl in reversed(trades):
        if pnl < 0:
            streak += 1
        else:
            break
    return streak


def get_risk_multiplier(client, limits: RiskLimits) -> tuple[float, int]:
    """Возвращает (multiplier, streak) - множитель для risk_pct НОВОЙ
    сделки, посчитанный по серии убытков подряд ПРЯМО СЕЙЧАС.

    1.0, пока серия короче limits.soft_derisk_after_losses.
    limits.soft_derisk_multiplier, начиная с этого порога (и до тех пор,
    пока не сработает жёсткий kill switch - см. check_new_position_allowed,
    он вызывается ОТДЕЛЬНО и раньше, эта функция не заменяет его, а
    только смягчает то, что происходит ДО его срабатывания).

    Намеренно НЕ кэширует и не понижает риск постепенно (0.75 -> 0.5 ->
    0.25...) - две ступени (обычный/сниженный) проще объяснить и
    предсказать, чем плавную кривую, а серия убытков и так штука редкая -
    сложная формула здесь не окупает добавленной непрозрачности.
    Использует ту же _consecutive_losses, что и check_new_position_allowed -
    единый источник правды про серию, а не два независимых подсчёта,
    которые могли бы разойтись при доработке одного без другого."""
    streak = _consecutive_losses(client, lookback=max(limits.max_consecutive_losses * 5, 20))
    if streak >= limits.soft_derisk_after_losses:
        return limits.soft_derisk_multiplier, streak
    return 1.0, streak


def _same_direction_open_count(open_positions: list, side: str) -> int:
    """Сколько из уже открытых позиций - в ТУ ЖЕ сторону, что и side
    новой сделки ("BUY"=лонг/"SELL"=шорт). Знак positionAmt в ответе
    Binance (см. FuturesClient.get_all_positions) - направление позиции:
    положительный = лонг, отрицательный = шорт."""
    is_long_side = side == "BUY"
    return sum(
        1 for p in open_positions
        if (float(p.get("positionAmt", 0)) > 0) == is_long_side
    )


def check_new_position_allowed(client, limits: RiskLimits, side: Optional[str] = None) -> Optional[str]:
    """None - можно открывать новую позицию. Иначе - строка с причиной
    отказа. Намеренно НЕ бросает исключение сама - futures_executor
    оборачивает результат в ExecutionError на своей стороне,
    единообразно с остальными отказами до входа (недостаточный баланс
    и т.п.).

    side - "BUY"/"SELL" направление НОВОЙ сделки, нужен только для
    лимита A4 (max_same_direction_positions, см. RiskLimits) - если не
    передан (None, дефолт для обратной совместимости со старыми
    вызывающими местами/тестами), проверка A4 просто пропускается, как
    будто лимита нет, а не отказывает вслепую."""
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

    if side is not None and limits.max_same_direction_positions is not None:
        same_dir = _same_direction_open_count(open_positions, side)
        if same_dir >= limits.max_same_direction_positions:
            direction_label = "лонг" if side == "BUY" else "шорт"
            return (
                f"уже открыто {same_dir}/{limits.max_same_direction_positions} позиций в сторону "
                f"{direction_label} - лимит на коррелированные позиции (не набирать несколько "
                "разных монет одной большой ставкой в одну сторону), новая позиция в ту же "
                "сторону не откроется, пока одна из текущих не закроется"
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
    long_count = sum(1 for p in open_positions if float(p.get("positionAmt", 0)) > 0)
    short_count = len(open_positions) - long_count
    loss_pct, baseline, current = _daily_loss_pct(client)
    streak = _consecutive_losses(client, lookback=max(limits.max_consecutive_losses * 5, 20))
    return {
        "kill_switch": kill_switch,
        "open_positions": len(open_positions),
        "open_positions_symbols": [p.get("symbol") for p in open_positions],
        "open_positions_long": long_count,
        "open_positions_short": short_count,
        "max_open_positions": limits.max_open_positions,
        "max_same_direction_positions": limits.max_same_direction_positions,
        "daily_loss_pct": round(loss_pct, 3),
        "daily_baseline": baseline,
        "daily_current": current,
        "max_daily_loss_pct": limits.max_daily_loss_pct,
        "consecutive_losses": streak,
        "max_consecutive_losses": limits.max_consecutive_losses,
    }